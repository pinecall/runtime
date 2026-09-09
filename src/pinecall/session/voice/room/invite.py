"""room.invite: a second SIP leg dialled into this same room, so the caller and it can talk."""

from __future__ import annotations

from livekit.protocol.sip import CreateSIPParticipantRequest

from pinecall.session.voice.room.holding import Holding
from pinecall_protocol.commands import RoomInvite

VERB = "room.invite"

# The room dials phones. A browser joins with a participate token the tenant minted; nothing on
# the media plane can pull one in, so asking is refused by name rather than quietly ignored.
NOT_DIALLED = "room.invite: a participant joins with a token; the room dials only kind sip"
NO_TRUNK = "room.invite: no outbound SIP trunk is configured, nothing can dial {to}"

# The identity a dialled leg takes in the room: livekit's own habit for SIP participants.
LEG_PREFIX = "sip_"


# The fact is the participant.joined the room writes when the far side answers, with kind sip;
# a leg that never answers is the error the API raises, in the log under this verb's name.
async def dialled(holding: Holding, wanted: RoomInvite) -> None:
    """CreateSIPParticipant into this call's room. Warm: the agent stays on with the caller."""
    if wanted.kind != "sip":
        holding.failed(VERB, NOT_DIALLED)
        return
    if holding.trunk is None:
        holding.failed(VERB, NO_TRUNK.format(to=wanted.to))
        return
    request = CreateSIPParticipantRequest(
        sip_trunk_id=holding.trunk,
        sip_call_to=wanted.to,
        room_name=holding.room.name,
        participant_identity=f"{LEG_PREFIX}{wanted.to}",
    )
    try:
        await holding.api.sip.create_sip_participant(request)
    except Exception as refused:  # noqa: BLE001 — every way the server says no is the same here
        holding.failed(VERB, str(refused))
