"""POST /v1/calls/{call}/listen: a supervisor's ear in a live call, hidden and silent."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api._deps import SettingsDep, SnapshotsDep, SuperviseKeyDep
from pinecall.tokens.seating import a_seat_in
from pinecall_protocol import WireModel

router = APIRouter()

# The scope whose row says what a listener may do: subscribe, never publish, and hidden, so the
# caller is never told anybody joined (types/token.py). The token joins the room the call IS.
A_LISTENER = "observe"


# The one answer both seat doors give, this one's and /supervise's: tokens/seating.py mints it.
class SeatTaken(WireModel):
    """A seat in a live call: where to connect, as whom, and for which org's person."""

    server_url: str
    participant_token: str
    call: str
    identity: str
    org: str
    subject: str | None
    name: str | None


@router.post("/v1/calls/{call}/listen")
async def listen(
    call: str, key: SuperviseKeyDep, snapshots: SnapshotsDep, settings: SettingsDep
) -> SeatTaken:
    """A token that hears one live call: {server_url, participant_token, call, identity}."""
    return SeatTaken(**await a_seat_in(call, A_LISTENER, key, snapshots, settings))
