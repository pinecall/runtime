"""The dispatch to a world's fleet, the rooms an agent is in, and LiveKit's signed events."""

import json
from collections.abc import Collection, Mapping

from google.protobuf.json_format import ParseDict, ParseError
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.webhook import WebhookEvent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pinecall.domain.errors import DeclarationRefused, NotAllowed
from pinecall.domain.names import Direction, Env, Json, JsonObject

NOT_A_ROOM_CONFIG = "room_config is not a LiveKit RoomConfiguration: {reason}"

# Room names ride one request's query, so they are params for in batches.
ROOMS_A_REQUEST = 100

# livekit's code for a room that is not there.
ROOM_GONE = "not_found"

UNSIGNED = "the event carries no signature of this box's LiveKit key: {reason}"

# The seats a person sits in: a browser's, and a phone's leg.
A_PERSON = frozenset({api.ParticipantInfo.Kind.STANDARD, api.ParticipantInfo.Kind.SIP})


# `trunk` names the carrier account the leg is dialled through; the worker asks the gateway for
# its inline configuration, so no secret rides a dispatch.
class Dialling(BaseModel):
    """The leg an outbound job places: trunk, far end, the number shown, the media's ceiling."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    trunk: str
    to: str
    shown: str
    max_duration_s: int = 0


# Written by the gateway alone, signed into a token or sent by the server API, so the worker
# trusts it: the scope a call runs in, and on an outbound call the ceiling of its leg.
class Dispatch(BaseModel):
    """What a dispatch tells the job it starts: whose call, which agent, and how it arrived."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    agent: str | None = None
    org: str | None = None
    env: Env | None = None
    holder: str | None = None
    direction: Direction = "inbound"
    caller: str | None = None
    # An opaque id the tenant's backend knows the visitor by; never a number or a name.
    contact: str | None = None
    # The token's scope: a dispatch that carries one spends its token once.
    scope: str | None = None
    # What the tenant's backend sealed into the token.
    metadata: JsonObject = Field(default_factory=dict[str, Json])
    dial: Dialling | None = None
    # The app socket that must serve the call (a spoken golden run).
    app: str | None = None
    # A simulated caller and its rules, frozen at dispatch so a later edit does not move them.
    run: str | None = None
    persona: str | None = None
    accepts_when: str | None = None
    declines_when: str | None = None
    diverted_from: Env | None = None
    # The call's worker went away mid-call: the job tells the caller once and closes the room.
    worker_gone: bool = False


def written(dispatch: Dispatch) -> str:
    """The dispatch as a job's metadata: compact JSON, absent fields absent."""
    data = dispatch.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return json.dumps(data, separators=(",", ":"))


# A job with no metadata or with somebody else's (a room nobody dispatched) reads as empty.
def read_dispatch(metadata: str) -> Dispatch:
    """The dispatch a job was started with."""
    if not metadata:
        return Dispatch()
    try:
        return Dispatch.model_validate_json(metadata)
    except ValidationError:
        return Dispatch()


# livekit creates the dispatch from the token when the join creates the room, so the browser
# cannot change the agent, the org or the world it reaches.
def room_dispatch(fleet: str, dispatch: Dispatch) -> RoomConfiguration:
    """The room configuration a visitor's token carries: one dispatch, to the world's fleet."""
    return RoomConfiguration(
        agents=[RoomAgentDispatch(agent_name=fleet, metadata=written(dispatch))]
    )


# livekit creates the room if it does not exist; its name is the call id the worker opens.
async def dispatched(server: api.LiveKitAPI, room: str, fleet: str, dispatch: Dispatch) -> None:
    """Send the world's fleet into a room: an outbound call, or a ring handed to a sandbox."""
    await server.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(room=room, agent_name=fleet, metadata=written(dispatch))
    )


# A stock livekit client names the agent as room_config.agents[0].agent_name, in either spelling.
def client_named_agent(room_config: Mapping[str, object] | None) -> str | None:
    """The agent a livekit client put in the room configuration, or None."""
    if not room_config:
        return None
    try:
        parsed = ParseDict(dict(room_config), RoomConfiguration(), ignore_unknown_fields=True)
    except ParseError as malformed:
        raise DeclarationRefused(NOT_A_ROOM_CONFIG.format(reason=malformed)) from malformed
    agents = list(parsed.agents)
    return (agents[0].agent_name or None) if agents else None


# A room is a live call only while an agent is in it: a browser tab or a supervisor keeps a
# room open long after its job is gone.
async def rooms_with_an_agent(server: api.LiveKitAPI, names: Collection[str]) -> set[str]:
    """The rooms of these that exist with an agent in them."""
    wanted = list(names)
    served: set[str] = set()
    for start in range(0, len(wanted), ROOMS_A_REQUEST):
        batch = wanted[start : start + ROOMS_A_REQUEST]
        rooms = await server.room.list_rooms(api.ListRoomsRequest(names=batch))
        for room in rooms.rooms:
            seats = await server.room.list_participants(api.ListParticipantsRequest(room=room.name))
            if any(seat.kind == api.ParticipantInfo.Kind.AGENT for seat in seats.participants):
                served.add(room.name)
    return served


async def room_closed(server: api.LiveKitAPI, name: str) -> None:
    """Delete the room, which hangs up everyone still in it; a room already gone is fine."""
    try:
        await server.room.delete_room(api.DeleteRoomRequest(room=name))
    except api.TwirpError as refused:
        if refused.code != ROOM_GONE:
            raise


# A room gone is nobody left in it.
async def left_alone(server: api.LiveKitAPI, name: str) -> bool:
    """Whether a person is still in the room and no agent is."""
    try:
        seats = await server.room.list_participants(api.ListParticipantsRequest(room=name))
    except api.TwirpError as refused:
        if refused.code != ROOM_GONE:
            raise
        return False
    kinds = {seat.kind for seat in seats.participants}
    return api.ParticipantInfo.Kind.AGENT not in kinds and bool(kinds & A_PERSON)


# livekit signs each event with a key of the server's: a token whose sha256 claim is the body's.
# The library raises a bare Exception for a body that does not match its token.
def livekit_event(body: str, token: str, key: str, secret: str) -> WebhookEvent:
    """The event of a body LiveKit signed with this key, or NotAllowed."""
    try:
        return api.WebhookReceiver(api.TokenVerifier(key, secret)).receive(body, token)
    except Exception as refused:
        raise NotAllowed(UNSIGNED.format(reason=refused)) from refused
