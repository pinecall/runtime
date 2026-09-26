"""room.invite: a second SIP leg dialled into this same room, so the caller and it can talk."""

from __future__ import annotations

from pinecall.session.voice.room.holding import Holding
from pinecall.session.voice.room.leg import a_leg
from pinecall_protocol.commands import RoomInvite

VERB = "room.invite"

# The room dials phones. A browser joins with a participate token the tenant minted; nothing on
# the media plane can pull one in, so asking is refused by name rather than quietly ignored.
NOT_DIALLED = "room.invite: a participant joins with a token; the room dials only kind sip"
NO_TRUNK = "room.invite: no outbound SIP trunk is configured, nothing can dial {to}"


# The fact is the participant.joined the room writes when the far side answers, with kind sip;
# a leg that never answers is the error the API raises, in the log under this verb's name.
async def dialled(holding: Holding, wanted: RoomInvite) -> None:
    """CreateSIPParticipant into this call's room. Warm: the agent stays on with the caller."""
    if wanted.kind != "sip":
        holding.failed(VERB, NOT_DIALLED)
        return
    # As a warm transfer does: the trunk comes back only when the org's guards let this number be
    # dialled, and the log gets the guard's own sentence when they do not.
    asked = await holding.trunks.outbound(wanted.to)
    if asked.trunk is None:
        holding.failed(VERB, asked.refused or NO_TRUNK.format(to=wanted.to))
        return
    try:
        await holding.api.sip.create_sip_participant(
            a_leg(asked.trunk, wanted.to, holding.room.name)
        )
    except Exception as refused:
        holding.failed(VERB, str(refused))
