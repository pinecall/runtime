"""POST /v1/calls/{call}/listen: a hidden, silent seat in a live call's room, on the key alone."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.auth.scopes import SCOPE_ATTRIBUTE
from pinecall.log.store import MemoryStore
from pinecall_protocol import decode_entries
from pinecall_protocol.fixtures import GOLDEN_LOG
from tests.api.conftest import A_KEY, AGENT

pytestmark = pytest.mark.unit

THE_CALL = "call_somebody_is_on"


async def a_call_in_progress(store: MemoryStore) -> None:
    """The golden log up to the moment the call is up: ringing, the room, the first turn."""
    for entry in decode_entries(GOLDEN_LOG.read_text()):
        await store.append(call=THE_CALL, agent=AGENT, type=entry.type, data=entry.data)
        if entry.type == "turn.user":
            return


async def a_call_that_ended(store: MemoryStore) -> None:
    """The whole golden log, sealed on its score: nobody is in that room any more."""
    for entry in decode_entries(GOLDEN_LOG.read_text()):
        await store.append(call=THE_CALL, agent=AGENT, type=entry.type, data=entry.data)


def knocked(gateway: TestClient, call: str, bearer: str = A_KEY) -> tuple[int, dict[str, Any]]:
    handle: Any = gateway
    answer: Any = handle.post(
        f"/v1/calls/{call}/listen", headers={"Authorization": f"Bearer {bearer}"}
    )
    status: int = answer.status_code
    said: dict[str, Any] = answer.json()
    return status, said


def payload_of(token: str) -> dict[str, Any]:
    _, payload, _ = token.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    decoded: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    return decoded


async def test_a_live_call_mints_a_hidden_listener_that_hears_and_never_speaks(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call_in_progress(store)
    status, said = knocked(gateway, THE_CALL)
    assert status == 200
    assert said["call"] == THE_CALL and said["identity"].startswith("sup_")
    claims = payload_of(said["participant_token"])
    video = claims["video"]
    assert video["room"] == THE_CALL and video["roomJoin"] is True
    assert video["canSubscribe"] is True and video["canPublish"] is False
    assert video["hidden"] is True
    assert claims["attributes"][SCOPE_ATTRIBUTE] == "observe"
    assert "roomConfig" not in claims, "a listener dispatches no agent: the call already has one"


async def test_a_call_that_ended_has_no_room_to_join_and_the_door_says_so(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call_that_ended(store)
    status, said = knocked(gateway, THE_CALL)
    assert status == 409
    assert "over" in said["detail"] and "recording" in said["detail"]


async def test_a_call_nobody_logged_is_a_404(gateway: TestClient) -> None:
    status, said = knocked(gateway, "call_nobody")
    assert status == 404
    assert "call_nobody" in said["detail"]


async def test_the_door_takes_the_api_key_and_nothing_else(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call_in_progress(store)
    status, _ = knocked(gateway, THE_CALL, bearer="not-a-key")
    assert status in {401, 403}
