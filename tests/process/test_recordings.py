"""Tests for where a recording is kept: the disk as always, or an S3 bucket under its org."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.process.recordings import (
    AUDIO_FILE,
    SEALED_FILE,
    Bucket,
    Disk,
    call_directory,
    kept_on_disk,
    recordings_of,
    served_sealed,
)
from pinecall.process.sealed_audio import new_key, seal_file
from pinecall.process.settings import Settings
from tests.fakes import bucket as fake

AUDIO = b"OggS" + bytes(range(96))


def a_recording(root: Path, call: str) -> Path:
    """The recorder's file for the call, as the worker leaves it."""
    directory = root / call
    directory.mkdir(parents=True)
    audio = directory / AUDIO_FILE
    audio.write_bytes(AUDIO)
    return audio


@pytest.fixture
def remote() -> fake.Bucket:
    return fake.Bucket()


@pytest.fixture
async def http(remote: fake.Bucket) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=remote.transport()) as client:
        yield client


async def read_whole(body: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in body])


def test_the_disk_unless_a_bucket_of_the_store_is_named(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    root = {"PINECALL_RECORDINGS": str(tmp_path)}
    assert recordings_of(Settings.model_validate(root), http) == Disk(tmp_path)
    store_alone = Settings.model_validate({**root, **fake.STORE_SETTINGS})
    assert recordings_of(store_alone, http) == Disk(tmp_path)
    named = Settings.model_validate(
        {**root, **fake.STORE_SETTINGS, "PINECALL_RECORDINGS_BUCKET": "b"}
    )
    assert recordings_of(named, http) == Bucket(tmp_path, "b", remote.store_on(http))


async def test_on_the_disk_the_file_stays_where_it_was_written_and_erasing_removes_it(
    tmp_path: Path,
) -> None:
    disk = Disk(tmp_path)
    audio = a_recording(tmp_path, "CA_1")
    await disk.store("org_1", "CA_1", audio)
    assert audio.read_bytes() == AUDIO
    assert await disk.fetch("org_1", "CA_1", None) is None
    assert await disk.erase("org_1", ["CA_1", "CA_never"]) == 1
    assert not audio.parent.exists()


async def test_a_stored_recording_moves_under_its_org_and_leaves_the_disk(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    audio = a_recording(tmp_path, "CA_1")
    await Bucket(tmp_path, remote.name, remote.store_on(http)).store("org_1", "CA_1", audio)
    assert remote.objects == {"org_1/CA_1/audio.ogg": AUDIO}
    assert not audio.parent.exists()


async def test_a_bucket_that_refuses_the_upload_leaves_the_file_on_the_disk(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    audio = a_recording(tmp_path, "CA_1")
    remote.refusal = 403
    with pytest.raises(UpstreamFailed, match="answered 403 to an upload of org_1/CA_1"):
        await Bucket(tmp_path, remote.name, remote.store_on(http)).store("org_1", "CA_1", audio)
    assert audio.read_bytes() == AUDIO


async def test_a_read_passes_the_range_on_and_a_missing_object_is_none(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    remote.objects["org_1/CA_1/audio.ogg"] = AUDIO
    kept = Bucket(tmp_path, remote.name, remote.store_on(http))
    part = await kept.fetch("org_1", "CA_1", "bytes=4-9")
    assert part is not None
    assert part.status == 206
    assert part.headers["content-range"] == f"bytes 4-9/{len(AUDIO)}"
    assert part.headers["content-length"] == "6"
    assert await read_whole(part.body) == AUDIO[4:10]
    whole = await kept.fetch("org_1", "CA_1", None)
    assert whole is not None
    assert (whole.status, await read_whole(whole.body)) == (200, AUDIO)
    assert await kept.fetch("org_1", "CA_2", None) is None
    assert await kept.fetch("org_2", "CA_1", None) is None


async def test_a_bucket_that_refuses_a_read_is_an_upstream_failure_not_a_missing_file(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    remote.refusal = 403
    with pytest.raises(UpstreamFailed, match="answered 403 to a read of"):
        await Bucket(tmp_path, remote.name, remote.store_on(http)).fetch("org_1", "CA_1", None)


async def test_erasing_deletes_each_calls_object_and_a_file_that_never_moved(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    calls = [f"CA_{n}" for n in range(20)]
    remote.objects = {f"org_1/{call}/audio.ogg": AUDIO for call in calls[:18]}
    remote.objects["org_2/CA_0/audio.ogg"] = AUDIO
    a_recording(tmp_path, "CA_18")
    kept = Bucket(tmp_path, remote.name, remote.store_on(http))
    assert await kept.erase("org_1", calls) == 19
    assert remote.objects == {"org_2/CA_0/audio.ogg": AUDIO}
    assert not (tmp_path / "CA_18").exists()


async def test_a_sealed_object_is_read_by_ranges_and_served_as_it_was_recorded(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    key, recorded = new_key(), AUDIO * 2000
    plain = tmp_path / "audio.ogg"
    plain.write_bytes(recorded)
    seal_file(plain, tmp_path / SEALED_FILE, key)
    remote.objects["org_1/CA_1/audio.sealed"] = (tmp_path / SEALED_FILE).read_bytes()
    kept = Bucket(tmp_path, remote.name, remote.store_on(http))
    sealed = await kept.sealed("org_1", "CA_1")
    assert sealed is not None
    part = served_sealed(sealed, key, "bytes=70000-70009")
    assert (part.status, part.headers["content-range"]) == (
        206,
        f"bytes 70000-70009/{len(recorded)}",
    )
    assert await read_whole(part.body) == recorded[70000:70010]
    whole = served_sealed(sealed, key, None)
    assert (whole.status, await read_whole(whole.body)) == (200, recorded)
    assert served_sealed(sealed, key, f"bytes={len(recorded)}-").status == 416
    assert await kept.sealed("org_1", "CA_2") is None


async def test_erasing_counts_a_call_kept_only_sealed(
    tmp_path: Path, remote: fake.Bucket, http: httpx.AsyncClient
) -> None:
    remote.objects = {"org_1/CA_1/audio.sealed": b"sealed", "org_1/CA_2/audio.ogg": AUDIO}
    kept = Bucket(tmp_path, remote.name, remote.store_on(http))
    assert await kept.erase("org_1", ["CA_1", "CA_2", "CA_3"]) == 2
    assert remote.objects == {}


def test_a_pointer_is_read_for_its_name_only_and_kept_under_the_root(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    assert (
        kept_on_disk(root, "c1", "/elsewhere/c9/audio.ogg") == (root / "c1" / AUDIO_FILE).resolve()
    )
    assert kept_on_disk(root, "c1", "audio.sealed") == (root / "c1" / SEALED_FILE).resolve()
    assert kept_on_disk(root, "c1", "/proc/self/environ") is None
    assert kept_on_disk(root, "c1", "/var/run/secrets/token") is None
    assert kept_on_disk(root, "..", "audio.ogg") is None


# A call id is a path component on the disk: one that would leave the root names nothing there.
def test_a_call_id_that_leaves_the_root_names_no_directory(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    (root / "call_1").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    assert call_directory(root, "call_1") == (root / "call_1").resolve()
    for outside in ("..", "../elsewhere", str(tmp_path / "elsewhere"), "", "."):
        assert call_directory(root, outside) is None
    disk = Disk(root)
    assert asyncio.run(disk.erase("org", ["..", "../elsewhere", "call_1"])) == 1
    assert (tmp_path / "elsewhere").is_dir()
    assert tmp_path.is_dir()
    assert not (root / "call_1").exists()
