"""A human's seat in a live call: the identity, the two refusals, and the token both doors mint."""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import HTTPException

from pinecall._settings import Settings
from pinecall.auth.keys import KeyRecord
from pinecall.auth.scopes import a_room_token, secret_for
from pinecall.log.snapshots import Snapshots

# The identity a human takes in the room, whether they came to listen or to speak, so the room's
# own facts (worker/bridge/room/facts.py) and a later verb name the same seat. The prefix is
# `sup_` for both: to the room, a listener and a supervisor are the same kind of visitor.
A_SEAT = "sup_"
SEAT_BYTES = 6

# Long enough to hear a call out; a desk that wants the next one asks again.
A_SEAT_LASTS_S = 15 * 60

NO_SUCH_CALL = "no call {call} on this gateway"
NOT_LIVE = "call {call} is over: nobody is in its room, read its log or its recording instead"


# Both seat doors take the API key, as every tenant door does: these scopes are the tenant's,
# never a visitor's. A call that is over has no room to join, and the door says so rather than
# minting a token LiveKit would refuse a minute later for a room that closed.
async def a_seat_in(
    call: str, scope: str, key: KeyRecord, snapshots: Snapshots, settings: Settings
) -> dict[str, Any]:
    """One seat in a live call: {server_url, participant_token, call, identity, org}."""
    snapshot = await snapshots.of(call)
    if snapshot is None:
        raise HTTPException(404, NO_SUCH_CALL.format(call=call))
    if not snapshot.live:
        raise HTTPException(409, NOT_LIVE.format(call=call))
    identity = f"{A_SEAT}{secrets.token_hex(SEAT_BYTES)}"
    token = a_room_token(call, scope, time.time() + A_SEAT_LASTS_S, secret_for(settings), identity)
    return {
        "server_url": settings.livekit_public_url or settings.livekit_url,
        "participant_token": token,
        "call": call,
        "identity": identity,
        "org": key.org,
    }
