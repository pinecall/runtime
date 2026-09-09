"""The caller's SIP leg, found in the room: the two numbers on it, and who acts on them."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass

from livekit import rtc
from livekit.agents.utils import wait_for_participant

from pinecall.types import Channel

# A phone call is a room job and the caller is a participant of it, so the leg can be a moment
# behind the agent. Bounded, because a verb that hangs is a caller left listening to silence.
WAIT_FOR_THE_LEG_S = 5.0

# The one channel with a leg to find. A console session, a widget and a chat are rooms nobody
# dialled: waiting there would be five seconds spent on a phone that is never coming.
THE_PHONE: Channel = "phone"

# Every SIP attribute livekit stamps on a phone leg starts so.
SIP_PREFIX = "sip."

# livekit's own names for the two numbers of a call: the number it came from, and the number that
# was dialled — which is the door, and therefore the route.
CALLER_NUMBER = f"{SIP_PREFIX}phoneNumber"
DIALLED_NUMBER = f"{SIP_PREFIX}trunkPhoneNumber"


@dataclass(frozen=True)
class Numbers:
    """The two numbers of a phone call, as livekit wrote them on the leg. None: not a phone."""

    caller: str | None = None
    dialled: str | None = None


# The one reader of livekit's SIP attributes: the router asks it who dialled what, the room's
# facts ask it whether a second leg is the caller. Nobody else spells these strings.
def the_numbers(attributes: Mapping[str, str]) -> Numbers:
    """What the SIP attributes of a seat say: who called, and the number they dialled."""
    return Numbers(
        caller=attributes.get(CALLER_NUMBER) or None,
        dialled=attributes.get(DIALLED_NUMBER) or None,
    )


# livekit's own wait does the subscribing (agents/utils/participant.py:153); what it does not do
# is give up, so the timeout is ours. A room that never connected raises there and has no leg
# either, which is the same answer.
async def the_sip_leg(
    room: rtc.Room, channel: Channel | None = None, wait: float = WAIT_FOR_THE_LEG_S
) -> rtc.RemoteParticipant | None:
    """The phone leg in this room, waited for briefly; None when this call has none to wait for."""
    if wait > 0 and _could_still_arrive(room, channel):
        with suppress(TimeoutError, RuntimeError):
            async with asyncio.timeout(wait):
                await wait_for_participant(room, kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP)
    return _a_phone_in(room)


# Two callers, two ways of knowing. A verb mid-call knows which door the call came in through and
# only a phone has a leg. The router does not know it yet — that is the question it is asking — so
# the room answers instead: an inbound leg is seated before the job is dispatched, and anybody
# else already sitting in the room is a browser, which means nobody dialled.
def _could_still_arrive(room: rtc.Room, channel: Channel | None) -> bool:
    """Whether a leg that is not in the room yet is still worth waiting for."""
    if _a_phone_in(room) is not None:
        return False
    return not room.remote_participants if channel is None else channel == THE_PHONE


# The caller is the phone that was already seated when the agent arrived, so the room's own order
# is the answer. A second leg is `room.invite`'s, and a call holding both is a warm transfer —
# out of scope, and said so in docs/decisions/sip.md.
def _a_phone_in(room: rtc.Room) -> rtc.RemoteParticipant | None:
    """The first participant livekit seated through SIP, in the order the room lists them."""
    for participant in room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return participant
    return None
