"""The leg of an outbound call: placed by the job that will answer on it, and how it did not."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

from pinecall_protocol import defs

logger = logging.getLogger(__name__)

# The identity the far end takes in the room, livekit's own habit for a SIP participant, and the
# one `seat.the_callers_seat` will pin the session's ears to.
LEG_PREFIX = "sip_"

# How long the far end may ring before this is a call nobody answered. Thirty seconds is about
# five rings on a mobile and one voicemail greeting away from picking itself up.
RINGING_S = 30

# SIP's own words for the three that are not a failure of ours. Everything else — a 4xx from the
# carrier, a trunk that is not configured, an address that does not resolve — is dial_failed, and
# the sentence the carrier answered with goes in the process log for whoever reads it.
BUSY = 486
DECLINED = 603
NO_ANSWER = (408, 480, 487)


@dataclass(frozen=True)
class Dialling:
    """What the dispatch told this job to place. It is never read from anywhere else."""

    trunk: str
    to: str
    shown: str
    max_duration_s: int


def asked_of(said: Any) -> Dialling | None:
    """The dial the dispatch carries, or None when this job is not one we placed."""
    if not isinstance(said, Mapping):
        return None
    wanted = cast("Mapping[str, Any]", said)
    trunk, to, shown = wanted.get("trunk"), wanted.get("to"), wanted.get("shown")
    if not isinstance(trunk, str) or not isinstance(to, str) or not isinstance(shown, str):
        return None
    ceiling = wanted.get("max_duration_s")
    seconds = int(ceiling) if isinstance(ceiling, int) and not isinstance(ceiling, bool) else 0
    return Dialling(trunk=trunk, to=to, shown=shown, max_duration_s=seconds)


# `wait_until_answered` is what makes busy and no-answer knowable at all: without it the request
# comes back as soon as the INVITE is sent and the only evidence left is a participant that never
# joins. It is also why this runs before the session is built — nothing is said into a room the
# far end never entered, and a call that was never answered costs one room for a few seconds.
async def placed(livekit: api.LiveKitAPI, room: str, dialling: Dialling) -> defs.EndReason | None:
    """The far end on the line, or the reason it is not. None is answered."""
    request = CreateSIPParticipantRequest(
        sip_trunk_id=dialling.trunk,
        sip_call_to=dialling.to,
        sip_number=dialling.shown,
        room_name=room,
        participant_identity=f"{LEG_PREFIX}{dialling.to}",
        wait_until_answered=True,
    )
    request.ringing_timeout.FromTimedelta(timedelta(seconds=RINGING_S))
    # The ceiling the org's policy set, enforced by the media plane and not by anything of ours:
    # a worker that crashed would otherwise leave a call running on somebody's bill.
    if dialling.max_duration_s:
        request.max_call_duration.FromTimedelta(timedelta(seconds=dialling.max_duration_s))
    try:
        await livekit.sip.create_sip_participant(request)
    except Exception as refused:
        reason = how_it_failed(refused)
        logger.info("the call to %s was not answered (%s): %s", dialling.to, reason, refused)
        return reason
    return None


def how_it_failed(refused: Exception) -> defs.EndReason:
    """The SIP response the carrier gave, in the protocol's own three words for it."""
    code = getattr(refused, "sip_status_code", None)
    if code in (BUSY, DECLINED):
        return "busy"
    if code in NO_ANSWER:
        return "no_answer"
    return "dial_failed"
