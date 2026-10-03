"""A person alone in a room: its newest dispatch, to a fleet and taken by none, offered a worker."""

from livekit import api
from livekit.protocol.agent_dispatch import AgentDispatch
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels import rooms
from pinecall.channels.offers import Offering

JOINED = "participant_joined"

LEFT = "participant_left"


# The SIP rule and a visitor's token dispatch to the fleet by its plain name, which no worker holds
# (each registers as <fleet>/<worker>), so LiveKit starts no job: the dispatch stays on the room
# with the call's metadata, and the gateway reads it there. A caller joining sends the first; a
# worker handing its caller to another fleet dispatches there and leaves, which sends the other.
# The newest dispatch decides: a call that ended, or a supervisor joining a live call, has the
# gateway's own dispatch, to a worker by its name, as the newest, and is left alone.
async def arrived(offering: Offering, event: WebhookEvent) -> str | None:
    """Offer the room a person is alone in to a worker; the name it went to, or None."""
    if not _a_person_may_be_alone(event):
        return None
    room = event.room.name
    if not await rooms.left_alone(offering.server, room):
        return None
    newest = _newest(await offering.server.agent_dispatch.list_dispatch(room))
    if newest is None or "/" in newest.agent_name or newest.state.jobs:
        return None
    return await offering.offer(room, newest.agent_name, rooms.read_dispatch(newest.metadata))


def _a_person_may_be_alone(event: WebhookEvent) -> bool:
    agent = event.participant.kind == api.ParticipantInfo.Kind.AGENT
    joined = event.event == JOINED and not agent
    return bool(event.room.name) and (joined or (event.event == LEFT and agent))


def _newest(dispatches: list[AgentDispatch]) -> AgentDispatch | None:
    return max(dispatches, key=lambda dispatch: dispatch.state.created_at, default=None)
