"""Which seat in the room is the caller's: the one voice the session listens to, and no other."""

from __future__ import annotations

from livekit import rtc

from pinecall.auth.scopes import SCOPE_ATTRIBUTE
from pinecall.session.voice import sip
from pinecall.types import Channel, Scope

# The scope a browser joins a voice call with: `talk` is the only one that publishes a microphone
# and the only one minted for the person the agent serves (types/token.py). The other two
# seats in the room are `observe`, hidden and silent, and `supervise`, which the desk holds and
# speaks from — and neither of them is who the agent answers.
THE_CALLERS_SCOPE: Scope = "talk"


# Pinned before the session subscribes to anything, because a room is not two people any more: a
# listener joins on an observe token and a supervisor publishes audio into the same
# room. Left to itself livekit links the first seat of an accepted kind (room_io.py:385-403), which
# is the caller by luck alone. See docs/decisions/voice-bridge.md.
async def the_callers_seat(room: rtc.Room, channel: Channel) -> str | None:
    """The identity the session hears on this call, or None when nobody is seated yet."""
    if channel == sip.THE_PHONE:
        leg = await sip.the_sip_leg(room, channel)
        return leg.identity if leg is not None else None
    return _the_talker_in(room)


# The order the room lists them in, as everywhere else the worker reads a seat: a call has one
# talk token and it was minted for one visit, so the first match is the only match.
def _the_talker_in(room: rtc.Room) -> str | None:
    """The identity of the browser that joined with a talk token, if it is here already."""
    for participant in room.remote_participants.values():
        if participant.attributes.get(SCOPE_ATTRIBUTE) == THE_CALLERS_SCOPE:
            return participant.identity
    return None
