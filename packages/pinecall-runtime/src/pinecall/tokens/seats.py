"""A human's seat in a live call: the identity, the two refusals, and the token both doors mint."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from pinecall.auth.keys import KeyRecord
from pinecall.errors import PinecallError
from pinecall.log.snapshots import Snapshots
from pinecall.settings import Settings
from pinecall.tokens.scopes import mint_room_token, secret_for
from pinecall.types.scopes import NAME_ATTRIBUTE, SUBJECT_ATTRIBUTE

# The identity a human takes in the room, whether they came to listen or to speak, so the room's
# own facts (session/voice/room/room_events.py) and a later verb name the same seat. The prefix is
# `sup_` for both: to the room, a listener and a supervisor are the same kind of visitor.
A_SEAT = "sup_"
SEAT_BYTES = 6

# Long enough to hear a call out; a desk that wants the next one asks again.
A_SEAT_LASTS_S = 15 * 60

NO_SUCH_CALL = "no call {call} on this gateway"
NOT_LIVE = "call {call} is over: nobody is in its room, read its log or its recording instead"


class NoSuchCall(PinecallError):
    """No call by that id on this gateway: nothing to take a seat in."""


class NotLive(PinecallError):
    """The call is over: its room is gone, and a token for it would be refused by the SFU."""


@dataclass(frozen=True)
class Seat:
    """A seat in a live call: where to connect, as whom, and for which org's person."""

    server_url: str
    participant_token: str
    call: str
    identity: str
    org: str
    subject: str | None
    name: str | None


# Both seat doors take the API key, as every tenant door does: these scopes are the tenant's,
# never a visitor's. A call that is over has no room to join, and the door says so rather than
# minting a token LiveKit would refuse a minute later for a room that closed.
async def mint_seat_token(
    call: str, scope: str, key: KeyRecord, snapshots: Snapshots, settings: Settings
) -> Seat:
    """One seat in a live call, minted for the person the key names; refused when it is over."""
    snapshot = await snapshots.of(call)
    if snapshot is None:
        raise NoSuchCall(NO_SUCH_CALL.format(call=call))
    if not snapshot.live:
        raise NotLive(NOT_LIVE.format(call=call))
    identity = f"{A_SEAT}{secrets.token_hex(SEAT_BYTES)}"
    # A person's key names the person, and the seat carries them: the member's id and name ride
    # the token as attributes, so the verb the desk sends from it is written down as theirs and
    # the room's own facts show a name beside the seat. An org's machine key names nobody.
    who = {
        attribute: value
        for attribute, value in ((SUBJECT_ATTRIBUTE, key.subject), (NAME_ATTRIBUTE, key.name))
        if value
    }
    token = mint_room_token(
        call, scope, time.time() + A_SEAT_LASTS_S, secret_for(settings), identity, attributes=who
    )
    return Seat(
        server_url=settings.livekit_public_url or settings.livekit_url,
        participant_token=token,
        call=call,
        identity=identity,
        org=key.org,
        subject=key.subject,
        name=key.name,
    )
