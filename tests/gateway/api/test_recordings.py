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


async def a_recorded_call(knocking: Knocking, audio: Path) -> CallContext:
    """A sealed call whose worker said its recording was written at the path."""
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        sealing = SealCallRequest(usage=[], outcome="booked", recording=str(audio))
        await worker.post(f"/v1/calls/{context.call}/sealed", json=sealing.written())
    return context


@postgres
async def test_a_recording_on_the_disk_is_served_whole_and_by_the_range_a_player_asks(
    knocking: Knocking, tmp_path: Path
) -> None:
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(AUDIO)
    context = await a_recorded_call(knocking, audio)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        whole = await tenant.get(f"/v1/calls/{context.call}/recording")
        part = await tenant.get(
            f"/v1/calls/{context.call}/recording", headers={"range": "bytes=4-9"}
        )
    assert (whole.status_code, whole.content) == (200, AUDIO)
    assert (part.status_code, part.content) == (206, AUDIO[4:10])
    assert part.headers["content-range"] == f"bytes 4-9/{len(AUDIO)}"


@postgres
async def test_with_a_bucket_a_recording_is_served_from_it_and_from_the_disk_until_it_moved(
    knocking: Knocking, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = await a_recorded_call(knocking, tmp_path / "gone" / "audio.ogg")
    still_here = tmp_path / "audio.ogg"
    still_here.write_bytes(AUDIO)
    waiting = await a_recorded_call(knocking, still_here)
    remote = Bucket(objects={f"{knocking.org.id}/{moved.call}/audio.ogg": AUDIO[::-1]})
    connections = knocking.gateway.connections
    settings = connections.settings.model_copy(
        update={
            "recordings_bucket": remote.name,
            "s3_endpoint": ENDPOINT,
            "s3_region": REGION,
            "s3_access_key_id": ACCESS_KEY_ID,
            "s3_secret_access_key": SECRET_ACCESS_KEY,
        }
    )
    async with httpx.AsyncClient(transport=remote.transport()) as http:
        with_a_bucket = replace(connections, settings=settings, http=http)
        monkeypatch.setattr(
            app.state, "gateway", replace(knocking.gateway, connections=with_a_bucket)
        )
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


async def a_sealed_call(knocking: Knocking, where: Path) -> tuple[CallContext, bytes]:
    """A sealed call whose worker sealed its recording under the call's key; the sealed bytes."""
    context = a_call(knocking)
    plain = where / "audio.ogg"
    plain.write_bytes(AUDIO)
    sealed = where / "audio.sealed"
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
    knocking: Knocking, tmp_path: Path
) -> None:
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
    (tmp_path / "audio.sealed").unlink()
    remote = Bucket(objects={f"{knocking.org.id}/{context.call}/audio.sealed": kept})
    connections = knocking.gateway.connections
    settings = connections.settings.model_copy(
        update={
            "recordings_bucket": remote.name,
            "s3_endpoint": ENDPOINT,
            "s3_region": REGION,
            "s3_access_key_id": ACCESS_KEY_ID,
            "s3_secret_access_key": SECRET_ACCESS_KEY,
        }
    )
    async with httpx.AsyncClient(transport=remote.transport()) as http:
        with_a_bucket = replace(connections, settings=settings, http=http)
        monkeypatch.setattr(
            app.state, "gateway", replace(knocking.gateway, connections=with_a_bucket)
        )
        async with knocking.http(knocking.app["sandbox"]) as tenant:
            part = await tenant.get(
                f"/v1/calls/{context.call}/recording", headers={"range": "bytes=100000-100009"}
            )
    assert (part.status_code, part.content) == (206, AUDIO[100000:100010])
