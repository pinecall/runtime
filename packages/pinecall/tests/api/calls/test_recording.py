"""GET /v1/calls/{call}/recording: the audio the summary points at, or why there is none."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.log.store import MemoryStore
from pinecall_protocol import decode_entries
from pinecall_protocol.fixtures import GOLDEN_LOG
from tests.api.conftest import A_KEY, AGENT

pytestmark = pytest.mark.unit

THE_CALL = "call_that_was_recorded"
THE_SUMMARY = "call.summary"


async def the_golden(store: MemoryStore, recording: str | None) -> None:
    """The golden log, with its summary pointing wherever this test says the audio is."""
    for entry in decode_entries(GOLDEN_LOG.read_text()):
        data = dict(entry.data)
        if entry.type == THE_SUMMARY:
            data["recording"] = recording
        await store.append(call=THE_CALL, agent=AGENT, type=entry.type, data=data)


def fetched(gateway: TestClient, call: str, headers: dict[str, str] | None = None) -> Any:
    handle: Any = gateway
    return handle.get(
        f"/v1/calls/{call}/recording",
        headers={"Authorization": f"Bearer {A_KEY}", **(headers or {})},
    )


async def test_the_audio_the_summary_points_at_is_served_as_ogg(
    gateway: TestClient, store: MemoryStore, tmp_path: Path
) -> None:
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS" + bytes(range(64)))
    await the_golden(store, str(audio))
    answer = fetched(gateway, THE_CALL)
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("audio/ogg")
    assert answer.content == audio.read_bytes()


async def test_a_player_may_ask_for_a_byte_range_and_seek(
    gateway: TestClient, store: MemoryStore, tmp_path: Path
) -> None:
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(bytes(range(100)))
    await the_golden(store, str(audio))
    answer = fetched(gateway, THE_CALL, {"Range": "bytes=10-19"})
    assert answer.status_code == 206
    assert answer.content == bytes(range(10, 20))


async def test_a_pointer_at_a_file_this_box_does_not_hold_says_where_it_is(
    gateway: TestClient, store: MemoryStore
) -> None:
    await the_golden(store, "recordings/elsewhere/audio.ogg")
    answer = fetched(gateway, THE_CALL)
    assert answer.status_code == 404
    assert "recordings/elsewhere/audio.ogg" in answer.json()["detail"]


async def test_a_call_that_kept_no_recording_says_so(
    gateway: TestClient, store: MemoryStore
) -> None:
    await the_golden(store, None)
    answer = fetched(gateway, THE_CALL)
    assert answer.status_code == 404
    assert "kept no recording" in answer.json()["detail"]


async def test_a_call_nobody_logged_is_a_404(gateway: TestClient) -> None:
    answer = fetched(gateway, "call_nobody")
    assert answer.status_code == 404
