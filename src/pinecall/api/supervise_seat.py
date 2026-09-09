"""POST /v1/calls/{call}/supervise: the desk's own seat in a live call, and its verbs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pinecall.api._deps import KeyDep, SettingsDep, SnapshotsDep
from pinecall.tokens.seating import a_seat_in

router = APIRouter()

# The scope whose row says what a supervisor may do: publish their microphone, hear the room, read
# this one call's log, and send the six verbs (domain/token.py). NOT hidden — livekit delivers no
# track from a hidden participant, so a hidden supervisor would take the line into a silence.
A_SUPERVISOR = "supervise"


# The twin of /listen, and the difference is the whole point: an ear is minted by `observe`, a
# voice by `supervise`, and the same token is what WS /v1/attach and POST /v1/calls/{call}/verbs
# take as the bearer. See docs/decisions/supervise.md.
@router.post("/v1/calls/{call}/supervise")
async def supervise(
    call: str, key: KeyDep, snapshots: SnapshotsDep, settings: SettingsDep
) -> dict[str, Any]:
    """A token that takes one live call's seat and sends its verbs, for fifteen minutes."""
    return await a_seat_in(call, A_SUPERVISOR, key, snapshots, settings)
