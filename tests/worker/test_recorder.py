"""Tests for the recording of a call's room, and where it is kept once written."""

import logging
import stat
from pathlib import Path

import httpx
import pytest

from pinecall.process.settings import Settings
from pinecall.worker._recorder import recording_path, stored
from tests.fakes.bucket import Bucket

AUDIO = b"OggS a call"


def test_a_call_gets_a_directory_of_its_own_the_recorder_may_write_in(tmp_path: Path) -> None:
    audio = recording_path(tmp_path, "call_1")
    assert audio == tmp_path / "call_1" / "audio.ogg"
    mode = (tmp_path / "call_1").stat().st_mode
    assert mode & stat.S_ISGID
    assert mode & stat.S_IWGRP


def reaching(monkeypatch: pytest.MonkeyPatch, remote: Bucket) -> None:
    """Every client the worker opens reaches the fake bucket and its metadata server."""
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda: real(transport=remote.transport()))


def settings_with(root: Path, bucket: str | None) -> Settings:
    named = {} if bucket is None else {"PINECALL_RECORDINGS_BUCKET": bucket}
    return Settings.model_validate({"PINECALL_RECORDINGS": str(root), **named})


async def test_a_written_recording_moves_to_the_bucket_under_its_org(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = Bucket()
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    await stored(settings_with(tmp_path, remote.name), "org_1", "CA_1", audio)
    assert remote.objects == {"org_1/CA_1/audio.ogg": AUDIO}
    assert not audio.exists()


async def test_a_box_without_a_bucket_keeps_the_recording_where_it_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = Bucket()
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    await stored(settings_with(tmp_path, None), "org_1", "CA_1", audio)
    assert (audio.read_bytes(), remote.asked) == (AUDIO, [])


async def test_a_bucket_that_refuses_leaves_the_recording_on_the_disk_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    remote = Bucket(refusal=503)
    reaching(monkeypatch, remote)
    audio = recording_path(tmp_path, "CA_1")
    audio.write_bytes(AUDIO)
    with caplog.at_level(logging.WARNING):
        await stored(settings_with(tmp_path, remote.name), "org_1", "CA_1", audio)
    assert audio.read_bytes() == AUDIO
    assert "the recording of CA_1 stays on this disk" in caplog.text
