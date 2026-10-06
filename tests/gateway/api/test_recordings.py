"""Tests for a call's recording as a player reads it: plain on a disk or in a bucket, or sealed."""

import base64
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from pinecall.domain.call import CallContext
from pinecall.gateway.app import app
from pinecall.process.sealed_audio import seal_file
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.fakes.bucket import ACCESS_KEY_ID, ENDPOINT, REGION, SECRET_ACCESS_KEY, Bucket
from tests.gateway.api.conftest import a_call

AUDIO = b"OggS" + bytes(range(60)) * 3000


def a_gateway_keeping(
    knocking: Knocking,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    remote: Bucket | None = None,
    http: httpx.AsyncClient | None = None,
) -> None:
    """The gateway with its recordings under the root, and in the bucket when one is given."""
    connections = knocking.gateway.connections
    update: dict[str, object] = {"recordings_root": str(root)}
    if remote is not None:
        update |= {
            "recordings_bucket": remote.name,
            "s3_endpoint": ENDPOINT,
            "s3_region": REGION,
            "s3_access_key_id": ACCESS_KEY_ID,
            "s3_secret_access_key": SECRET_ACCESS_KEY,
        }
    settings = connections.settings.model_copy(update=update)
    kept = replace(connections, settings=settings, http=http or connections.http)
    monkeypatch.setattr(app.state, "gateway", replace(knocking.gateway, connections=kept))


def recording_file(root: Path, context: CallContext, name: str = "audio.ogg") -> Path:
    """Where the worker leaves the call's recording."""
    directory = root / context.call
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


async def sealed_with(knocking: Knocking, context: CallContext, pointer: str) -> None:
    """The call opened and sealed by its worker, saying its recording was kept at the pointer."""
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        sealing = SealCallRequest(usage=[], outcome="booked", recording=pointer)
        await worker.post(f"/v1/calls/{context.call}/sealed", json=sealing.written())


@postgres
async def test_a_recording_on_the_disk_is_served_whole_and_by_the_range_a_player_asks(
    knocking: Knocking, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_gateway_keeping(knocking, monkeypatch, tmp_path)
    context = a_call(knocking)
    audio = recording_file(tmp_path, context)
    audio.write_bytes(AUDIO)
    await sealed_with(knocking, context, str(audio))
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        whole = await tenant.get(f"/v1/calls/{context.call}/recording")
        part = await tenant.get(
            f"/v1/calls/{context.call}/recording", headers={"range": "bytes=4-9"}
        )
    assert (whole.status_code, whole.content) == (200, AUDIO)
    assert (part.status_code, part.content) == (206, AUDIO[4:10])
    assert part.headers["content-range"] == f"bytes 4-9/{len(AUDIO)}"


@postgres
async def test_a_pointer_that_names_any_other_file_serves_nothing(
    knocking: Knocking, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_gateway_keeping(knocking, monkeypatch, tmp_path / "recordings")
    elsewhere = tmp_path / "hostname"
    elsewhere.write_bytes(b"a file of the gateway's own")
    outside = a_call(knocking)
    await sealed_with(knocking, outside, str(elsewhere))
    # Named as a recording but in another directory: read for its name only, under the root.
    misplaced = a_call(knocking)
    (tmp_path / "audio.ogg").write_bytes(AUDIO)
    await sealed_with(knocking, misplaced, str(tmp_path / "audio.ogg"))
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        refused = await tenant.get(f"/v1/calls/{outside.call}/recording")
        not_there = await tenant.get(f"/v1/calls/{misplaced.call}/recording")
    assert refused.status_code == 404
    assert refused.json()["detail"] == f"call {outside.call} kept no recording"
    assert not_there.status_code == 404


@postgres
async def test_with_a_bucket_a_recording_is_served_from_it_and_from_the_disk_until_it_moved(
    knocking: Knocking, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = a_call(knocking)
    await sealed_with(knocking, moved, str(recording_file(tmp_path, moved)))
    waiting = a_call(knocking)
    still_here = recording_file(tmp_path, waiting)
    still_here.write_bytes(AUDIO)
    await sealed_with(knocking, waiting, str(still_here))
    remote = Bucket(objects={f"{knocking.org.id}/{moved.call}/audio.ogg": AUDIO[::-1]})
    async with httpx.AsyncClient(transport=remote.transport()) as http:
        a_gateway_keeping(knocking, monkeypatch, tmp_path, remote, http)
        async with knocking.http(knocking.app["sandbox"]) as tenant:
            ranged = await tenant.get(
                f"/v1/calls/{moved.call}/recording", headers={"range": "bytes=0-3"}
            )
            local = await tenant.get(f"/v1/calls/{waiting.call}/recording")
    assert (ranged.status_code, ranged.content) == (206, AUDIO[::-1][:4])
    assert ranged.headers["content-range"] == f"bytes 0-3/{len(AUDIO)}"
    assert ranged.headers["content-length"] == "4"
    assert ranged.headers["content-type"] == "audio/ogg"
    assert (local.status_code, local.content) == (200, AUDIO)


async def a_sealed_call(knocking: Knocking, root: Path) -> tuple[CallContext, bytes]:
    """A sealed call whose worker sealed its recording under the call's key; the sealed bytes."""
    context = a_call(knocking)
    plain = recording_file(root, context)
    plain.write_bytes(AUDIO)
    sealed = recording_file(root, context, "audio.sealed")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        given = await worker.post(f"/v1/calls/{context.call}/recording/key")
        again = await worker.post(f"/v1/calls/{context.call}/recording/key")
        assert given.json() == again.json()
        seal_file(plain, sealed, base64.urlsafe_b64decode(given.json()["key"]))
        sealing = SealCallRequest(usage=[], outcome="booked", recording=str(sealed))
        await worker.post(f"/v1/calls/{context.call}/sealed", json=sealing.written())
    return context, sealed.read_bytes()


@postgres
async def test_a_sealed_recording_opens_whole_by_the_range_asked_and_not_past_its_end(
    knocking: Knocking, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_gateway_keeping(knocking, monkeypatch, tmp_path)
    context, _ = await a_sealed_call(knocking, tmp_path)
    url = f"/v1/calls/{context.call}/recording"
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        whole = await tenant.get(url)
        part = await tenant.get(url, headers={"range": "bytes=65530-65545"})
        tail = await tenant.get(url, headers={"range": "bytes=-7"})
        past = await tenant.get(url, headers={"range": f"bytes={len(AUDIO)}-"})
    assert (whole.status_code, whole.content) == (200, AUDIO)
    assert whole.headers["content-length"] == str(len(AUDIO))
    assert (part.status_code, part.content) == (206, AUDIO[65530:65546])
    assert part.headers["content-range"] == f"bytes 65530-65545/{len(AUDIO)}"
    assert (tail.status_code, tail.content) == (206, AUDIO[-7:])
    assert past.status_code == 416
    assert past.headers["content-range"] == f"bytes */{len(AUDIO)}"


@postgres
async def test_a_sealed_recording_in_the_bucket_is_read_by_ranges_and_opened(
    knocking: Knocking, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, kept = await a_sealed_call(knocking, tmp_path)
    recording_file(tmp_path, context, "audio.sealed").unlink()
    remote = Bucket(objects={f"{knocking.org.id}/{context.call}/audio.sealed": kept})
    async with httpx.AsyncClient(transport=remote.transport()) as http:
        a_gateway_keeping(knocking, monkeypatch, tmp_path, remote, http)
        async with knocking.http(knocking.app["sandbox"]) as tenant:
            part = await tenant.get(
                f"/v1/calls/{context.call}/recording", headers={"range": "bytes=100000-100009"}
            )
    assert (part.status_code, part.content) == (206, AUDIO[100000:100010])
