"""POST /v1/calls/{call}/supervise: the desk's seat in a live call — heard, and seen."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.auth.scopes import SCOPE_ATTRIBUTE
from pinecall.log.store import MemoryStore
from tests.api.conftest import A_KEY
from tests.api.tokens.test_listen_door import (
    THE_CALL,
    a_call_in_progress,
    a_call_that_ended,
    payload_of,
)

pytestmark = pytest.mark.unit


def knocked(gateway: TestClient, call: str, bearer: str = A_KEY) -> tuple[int, dict[str, Any]]:
    handle: Any = gateway
    answer: Any = handle.post(
        f"/v1/calls/{call}/supervise", headers={"Authorization": f"Bearer {bearer}"}
    )
    status: int = answer.status_code
    said: dict[str, Any] = answer.json()
    return status, said


async def test_a_live_call_mints_a_supervisor_who_can_be_heard_and_is_not_hidden(
    gateway: TestClient, store: MemoryStore
) -> None:
    """A hidden participant publishes to nobody in livekit: a hidden takeover is a silent one."""
    await a_call_in_progress(store)
    status, said = knocked(gateway, THE_CALL)
    assert status == 200
    assert said["call"] == THE_CALL and said["identity"].startswith("sup_")
    claims = payload_of(said["participant_token"])
    video = claims["video"]
    assert video["room"] == THE_CALL and video["roomJoin"] is True
    assert video["canPublish"] is True and video["canSubscribe"] is True
    assert video["canPublishSources"] == ["microphone"]
    assert "hidden" not in video or video["hidden"] is False
    assert claims["attributes"][SCOPE_ATTRIBUTE] == "supervise"
    assert "roomConfig" not in claims, "a supervisor dispatches no agent: the call already has one"


async def test_a_call_that_ended_has_no_line_to_take_and_the_door_says_so(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call_that_ended(store)
    status, said = knocked(gateway, THE_CALL)
    assert status == 409 and "recording" in said["detail"]


async def test_a_call_nobody_logged_is_a_404(gateway: TestClient) -> None:
    status, said = knocked(gateway, "call_nobody")
    assert status == 404 and "call_nobody" in said["detail"]


async def test_the_door_takes_the_api_key_and_nothing_else(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call_in_progress(store)
    status, _ = knocked(gateway, THE_CALL, bearer="not-a-key")
    assert status in {401, 403}
