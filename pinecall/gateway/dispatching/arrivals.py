"""A person alone in a room is offered a worker; one an agent joined, or that ended, is let go."""

from livekit import api
from livekit.protocol.agent_dispatch import AgentDispatch
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels import offers, rooms
from pinecall.channels.offers import Offering
from pinecall.channels.telephony.hand_over import handed_in
from pinecall.domain.names import Env

JOINED = "participant_joined"

LEFT = "participant_left"

FINISHED = "room_finished"


# The SIP rule and a visitor's token dispatch to the fleet by its plain name, which no worker holds
# (each registers as <fleet>/<worker>), so LiveKit starts no job: the dispatch stays on the room
# with the call's metadata, and the gateway reads it there. A caller joining sends the first; a
# worker handing its caller to another fleet dispatches there and leaves, which sends the other.
# The newest dispatch decides: a call that ended, or a supervisor joining a live call, has the
# gateway's own dispatch, to a worker by its name, as the newest, and is left alone. The room is
# asked of the LiveKit of the world that sent the event. A ring production handed to the sandbox
# over SIP is made the developer's by what its leg, the person joining, carries.
async def arrived(offering: Offering, world: Env, event: WebhookEvent) -> str | None:
    """Offer the room a person is alone in to a worker; the name it went to, or None."""
    if not _a_person_may_be_alone(event):
        return None
    room = event.room.name
    server = offering.servers[world]
    if not await rooms.left_alone(server, room):
        return None
    newest = _newest(await server.agent_dispatch.list_dispatch(room))
    if newest is None or "/" in newest.agent_name or newest.state.jobs:
        return None
    dispatch = handed_in(rooms.read_dispatch(newest.metadata), event.participant.attributes)
    return await offering.offer(room, newest.agent_name, dispatch)


# A dispatch creates the room it names again if it is gone: a room offered after its caller hung
# up, or after the worker sent to say the sentence closed it, would be a job on an empty room.
async def settled(offering: Offering, event: WebhookEvent) -> None:
    """Let a room go that an agent joined or that ended: nothing is left to offer it to."""
    agent_in = event.event == JOINED and event.participant.kind == api.ParticipantInfo.Kind.AGENT
    if event.room.name and (agent_in or event.event == FINISHED):
        await offers.forgotten(offering.pool, event.room.name)


def _a_person_may_be_alone(event: WebhookEvent) -> bool:
    agent = event.participant.kind == api.ParticipantInfo.Kind.AGENT
    joined = event.event == JOINED and not agent
    return bool(event.room.name) and (joined or (event.event == LEFT and agent))


def _newest(dispatches: list[AgentDispatch]) -> AgentDispatch | None:
    return max(dispatches, key=lambda dispatch: dispatch.state.created_at, default=None)
