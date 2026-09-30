"""Tests for the recording of a call's room, and where it is kept once written."""

import base64
import logging
import stat
from http import HTTPStatus
from pathlib import Path

import httpx
import pytest

from pinecall.fleet.client import GatewayClient
from pinecall.process.sealed_audio import Span, new_key, on_disk, opened, recorded_size
from pinecall.process.settings import Settings
from pinecall.worker._recorder import recording_path, stored
from tests.fakes.bucket import STORE_SETTINGS, Bucket

AUDIO = b"OggS a call"

KEY = new_key()


def test_a_call_gets_a_directory_of_its_own_the_recorder_may_write_in(tmp_path: Path) -> None:
    audio = recording_path(tmp_path, "call_1")
    assert audio == tmp_path / "call_1" / "audio.ogg"
    mode = (tmp_path / "call_1").stat().st_mode
    assert mode & stat.S_ISGID
    assert mode & stat.S_IWGRP


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
    assert (kept.exists(), audio.exists(), remote.asked) == (True, False, [])
    assert await opened_whole(kept) == AUDIO


async def test_a_gateway_that_seals_no_recording_leaves_it_as_it_was_written_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = gateway_answering(HTTPStatus.NOT_FOUND)
    remote = Bucket()
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    with caplog.at_level(logging.WARNING):
        kept = await stored(settings_with(tmp_path, remote.name), gateway, "org_1", "CA_1", audio)
    assert kept == audio
    assert remote.objects == {"org_1/CA_1/audio.ogg": AUDIO}
    assert "the recording of CA_1 is kept as it was written" in caplog.text


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
    assert await opened_whole(kept) == AUDIO
    assert "the recording of CA_1 stays on this disk" in caplog.text
