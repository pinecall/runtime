"""POST /v1/calls/{call}/listen: a supervisor's ear in a live call, hidden and silent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pinecall.api._deps import KeyDep, SettingsDep, SnapshotsDep
from pinecall.tokens.seating import a_seat_in

router = APIRouter()

# The scope whose row says what a listener may do: subscribe, never publish, and hidden, so the
# caller is never told anybody joined (domain/token.py). The token joins the room the call IS.
A_LISTENER = "observe"


@router.post("/v1/calls/{call}/listen")
async def listen(
    call: str, key: KeyDep, snapshots: SnapshotsDep, settings: SettingsDep
) -> dict[str, Any]:
    """A token that hears one live call: {server_url, participant_token, call, identity}."""
    return await a_seat_in(call, A_LISTENER, key, snapshots, settings)
