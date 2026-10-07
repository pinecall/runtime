"""Tests for where a call's recording is written, and where it is kept once closed."""

import base64
import logging
from http import HTTPStatus
from pathlib import Path

import httpx
import pytest

from pinecall.fleet.client import GatewayClient
from pinecall.process.recordings import recording_path
from pinecall.process.sealed_audio import Span, new_key, on_disk, opened, recorded_size
from pinecall.process.settings import Settings
from pinecall.worker import _recorder
from pinecall.worker._recorder import stored, written
from tests.fakes.bucket import STORE_SETTINGS, Bucket

AUDIO = b"OggS a call"

KEY = new_key()


def test_a_call_that_ended_before_its_session_recorded_has_no_file(tmp_path: Path) -> None:
    audio = recording_path(tmp_path, "call_1")
    assert not written(audio)
    audio.write_bytes(b"")
    assert not written(audio)
    audio.write_bytes(AUDIO)
    assert written(audio)


def reaching(monkeypatch: pytest.MonkeyPatch, remote: Bucket) -> None:
    """Every client the worker opens reaches the fake bucket."""
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda: real(transport=remote.transport()))


def settings_with(root: Path, bucket: str | None) -> Settings:
    named = {} if bucket is None else {"PINECALL_RECORDINGS_BUCKET": bucket, **STORE_SETTINGS}
    return Settings.model_validate({"PINECALL_RECORDINGS": str(root), **named})


def gateway_answering(status: int) -> GatewayClient:
    """A gateway whose key door answers with the status: the key, or a refusal."""

    def answer(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/v1/calls/CA_1/recording/key")
        if status != HTTPStatus.OK:
            return httpx.Response(status, json={"detail": "no"})
        return httpx.Response(status, json={"key": base64.urlsafe_b64encode(KEY).decode()})

    transport = httpx.MockTransport(answer)
    return GatewayClient(httpx.AsyncClient(base_url="http://gateway.test", transport=transport))


async def opened_whole(path: Path) -> bytes:
    sealed = await on_disk("CA_1", path)
    assert sealed is not None
    span = Span(0, recorded_size(sealed.size) - 1)
    return b"".join([piece async for piece in opened(sealed, KEY, span)])


async def test_a_written_recording_is_sealed_under_the_calls_key_and_moves_to_the_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = gateway_answering(HTTPStatus.OK)
    remote = Bucket()
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    kept = await stored(settings_with(tmp_path, remote.name), gateway, "org_1", "CA_1", audio)
    assert kept == audio.with_name("audio.sealed")
    assert list(remote.objects) == ["org_1/CA_1/audio.sealed"]
    assert AUDIO not in remote.objects["org_1/CA_1/audio.sealed"]
    assert not audio.parent.exists()
    tmp_path.joinpath("opened.sealed").write_bytes(remote.objects["org_1/CA_1/audio.sealed"])
    assert await opened_whole(tmp_path / "opened.sealed") == AUDIO


async def test_a_box_without_a_bucket_keeps_the_sealed_recording_where_it_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = gateway_answering(HTTPStatus.OK)
    remote = Bucket()
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    kept = await stored(settings_with(tmp_path, None), gateway, "org_1", "CA_1", audio)
    assert kept is not None
    assert (kept.exists(), audio.exists(), remote.asked) == (True, False, [])
    assert await opened_whole(kept) == AUDIO


# Never kept as it was written: a gateway that refuses the key, or stays away past every wait,
# and the recording is removed, nothing reaches the bucket, and the summary names none.
@pytest.mark.parametrize(
    "status", [HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT, HTTPStatus.BAD_GATEWAY]
)
async def test_a_recording_with_no_key_is_dropped_and_never_stored_plain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status: HTTPStatus,
) -> None:
    monkeypatch.setattr(_recorder, "KEY_WAITS_S", (0.0, 0.0))
    gateway = gateway_answering(status)
    remote = Bucket()
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    with caplog.at_level(logging.WARNING):
        kept = await stored(settings_with(tmp_path, remote.name), gateway, "org_1", "CA_1", audio)
    assert kept is None
    assert (remote.asked, audio.exists(), audio.with_name("audio.sealed").exists()) == (
        [],
        False,
        False,
    )
    assert "the recording of CA_1 is dropped, never kept unsealed" in caplog.text


# A gateway away for a moment is asked again: the second answer seals the recording.
async def test_a_gateway_away_for_a_moment_is_asked_again_for_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_recorder, "KEY_WAITS_S", (0.0, 0.0))
    answers = [HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.OK]

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/calls/CA_1/recording/key"
        status = answers.pop(0)
        if status != HTTPStatus.OK:
            return httpx.Response(status, json={"detail": "away"})
        return httpx.Response(status, json={"key": base64.urlsafe_b64encode(KEY).decode()})

    transport = httpx.MockTransport(answer)
    gateway = GatewayClient(httpx.AsyncClient(base_url="http://gateway.test", transport=transport))
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    kept = await stored(settings_with(tmp_path, None), gateway, "org_1", "CA_1", audio)
    assert kept is not None
    assert answers == []
    assert await opened_whole(kept) == AUDIO


async def test_a_bucket_that_refuses_leaves_the_recording_on_the_disk_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = gateway_answering(HTTPStatus.OK)
    remote = Bucket(refusal=503)
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    with caplog.at_level(logging.WARNING):
        kept = await stored(settings_with(tmp_path, remote.name), gateway, "org_1", "CA_1", audio)
    assert kept is not None
    assert await opened_whole(kept) == AUDIO
    assert "the recording of CA_1 stays on this disk" in caplog.text
