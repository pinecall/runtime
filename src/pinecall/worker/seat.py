"""Which seat in the room is the caller's: the one voice the session listens to, and no other."""

from __future__ import annotations

import asyncio
from typing import Literal

from livekit import rtc

from pinecall.session.voice import sip
from pinecall.types import THE_WIDGET, Channel, Scope
from pinecall.types.token import SCOPE_ATTRIBUTE

# The scope a browser joins a voice call with: `talk` is the only one that publishes a microphone
# and the only one minted for the person the agent serves (types/token.py). The other two
# seats in the room are `observe`, hidden and silent, and `supervise`, which the desk holds and
# speaks from — and neither of them is who the agent answers.
THE_CALLERS_SCOPE: Scope = "talk"

# livekit's second arrival event: a seat is `connected` first and `active` once its state settles,
# and the library's own waits listen on this one (agents/utils/participant.py:190).
ACTIVE: Literal["participant_active"] = "participant_active"

# A browser's seat can be a moment behind the agent, exactly as a phone leg can, and until this
# was waited for the difference decided whether a call was RECORDED. A dispatch that NAMES its
# agent is waited for nowhere — `worker/router.py:51` passes NOT_WAITED_FOR — and a simulated
# caller always names it: `evals/calling.py:101` dispatches and only then connects the caller and
# publishes its voice. Reaching the session with nobody seated leaves the agent's `input.audio`
# unset, and livekit builds no recorder at all when it is (agent_session.py:1035), once, at start.
# So the race was silent and its prize was the whole recording. The same five seconds the leg
# gets, for the same reason — see sip.WAIT_FOR_THE_LEG_S.
WAIT_FOR_THE_CALLER_S = 5.0


# Pinned before the session subscribes to anything, because a room is not two people any more: a
# listener joins on an observe token and a supervisor publishes audio into the same
# room. Left to itself livekit links the first seat of an accepted kind (room_io.py:385-403), which
# is the caller by luck alone. See docs/decisions/voice-bridge.md.
async def the_callers_seat(
    room: rtc.Room, channel: Channel, wait: float = WAIT_FOR_THE_CALLER_S, *, spoken: bool = True
) -> str | None:
    """The identity the session hears on this call, or None when nobody is seated yet."""
    # A written visit — a `chat` token — publishes no voice and its seat carries no talk scope, so
    # the wait below could only time out: five seconds of silence before the greeting, and the
    # first thing typed dropped on a callback not yet attached (2026-09-16, `seat: 5.0` on the
    # live line). Nothing to pin, as with WhatsApp.
    if not spoken:
        return None
    if channel == sip.THE_PHONE:
        leg = await sip.the_sip_leg(room, channel)
        return leg.identity if leg is not None else None
    # WhatsApp is a room nobody joins: every turn arrives as text, so there is no seat coming and
    # five seconds spent here would be five seconds of a written reply nobody is waiting on.
    if channel != THE_WIDGET:
        return None
    return _the_talker_in(room) or await _until_the_talker_joins(room, wait)


# The order the room lists them in, as everywhere else the worker reads a seat: a call has one
# talk token and it was minted for one visit, so the first match is the only match.
def _the_talker_in(room: rtc.Room) -> str | None:
    """The identity of the browser that joined with a talk token, if it is here already."""
    for participant in room.remote_participants.values():
        if participant.attributes.get(SCOPE_ATTRIBUTE) == THE_CALLERS_SCOPE:
            return participant.identity
    return None


# The scope rides the token, so it is on the seat the moment livekit seats it and there is no
# second event to wait for. A room that never connected has nobody coming and says so at once,
# which is the same answer the leg's wait gives (session/voice/sip.py:57).
async def _until_the_talker_joins(room: rtc.Room, wait: float) -> str | None:
    """The talk seat, waited for briefly; None when none took it in the time it had."""
    if wait <= 0 or not room.isconnected():
        return None
    seated = asyncio.Future[str]()

    def _if_it_is_the_caller(participant: rtc.RemoteParticipant) -> None:
        if participant.attributes.get(SCOPE_ATTRIBUTE) == THE_CALLERS_SCOPE and not seated.done():
            seated.set_result(participant.identity)

    room.on(  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        ACTIVE, _if_it_is_the_caller
    )
    try:
        # Asked again now that the listener is on: a seat that arrived between the two reads would
        # otherwise be missed by both, and waited out for nothing.
        already_here = _the_talker_in(room)
        if already_here is not None:
            return already_here
        async with asyncio.timeout(wait):
            return await seated
    except (TimeoutError, RuntimeError):
        return None
    finally:
        room.off(  # pyright: ignore[reportUnknownMemberType] — the same signature, unsubscribing
            ACTIVE, _if_it_is_the_caller
        )
