"""participant.remove: somebody put out of the room; the caller put out ends the call."""

from __future__ import annotations

from livekit.protocol.room import RoomParticipantIdentity

from pinecall.session.voice.room.holding import Holding
from pinecall_protocol.commands import ParticipantRemove

VERB = "participant.remove"


# The fact is the participant.left the room writes as they go, with livekit's own reason
# participant_removed: the room saw them leave, so the room says so, and the verb says nothing.
async def removed(holding: Holding, wanted: ParticipantRemove) -> None:
    """RemoveParticipant, by identity, from this call's room."""
    request = RoomParticipantIdentity(room=holding.room.name, identity=wanted.identity)
    try:
        await holding.api.room.remove_participant(request)
    except Exception as refused:
        holding.failed(VERB, str(refused))
