"""One SIP leg into a room, as every dial of this runtime asks LiveKit for it."""

from __future__ import annotations

from datetime import timedelta

from livekit.protocol.sip import CreateSIPParticipantRequest

# The identity a dialled leg takes in the room: livekit's own habit for SIP participants, and
# the same one every second leg of this runtime wears — a call placed, a warm transfer, a
# room.invite — so a reader of the log sees one kind of second leg, and `seat.the_callers_seat`
# can pin the session's ears to it.
LEG_PREFIX = "sip_"


def leg_identity(to: str) -> str:
    """What the far end is called in the room."""
    return f"{LEG_PREFIX}{to}"


def a_leg(
    trunk: str,
    to: str,
    room: str,
    *,
    shown: str | None = None,
    wait_until_answered: bool = False,
    ringing_s: float | None = None,
    play_dialtone: bool = False,
    max_duration_s: int = 0,
) -> CreateSIPParticipantRequest:
    """The request, with only what this dial asks for set on it: `wait_until_answered` is what
    makes busy and no-answer knowable at all, `ringing_s` how long the far end may ring, the
    dial tone what the room hears meanwhile, and `max_duration_s` the ceiling the media plane
    enforces when a worker is no longer there to."""
    request = CreateSIPParticipantRequest(
        sip_trunk_id=trunk,
        sip_call_to=to,
        room_name=room,
        participant_identity=leg_identity(to),
    )
    if shown is not None:
        request.sip_number = shown
    if wait_until_answered:
        request.wait_until_answered = True
    if play_dialtone:
        request.play_dialtone = True
    if ringing_s is not None:
        request.ringing_timeout.FromTimedelta(timedelta(seconds=ringing_s))
    if max_duration_s:
        request.max_call_duration.FromTimedelta(timedelta(seconds=max_duration_s))
    return request
