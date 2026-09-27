"""Which agent answers a number, and the dispatch that sends a call to its world's fleet."""

import json
import logging
from collections.abc import Collection, Mapping, Sequence

from google.protobuf.json_format import ParseDict, ParseError
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pinecall.domain.errors import DeclarationRefused, NotAvailable
from pinecall.domain.settings import Settings
from pinecall.domain.types import Channel, Direction, Env, Json, JsonObject, Route
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

OF_ORG = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE org = %(org)s AND env = %(env)s
ORDER BY added_at, number
"""
# The schema lets two orgs type the same number: the oldest row answers, the others are named.
AT = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE channel = %(channel)s AND number = %(number)s
ORDER BY added_at
"""
TWO_ORGS = "%s answers in org %s: org %s typed the same number, and the older row answers"
OF_NUMBER = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE org = %(org)s AND number = %(number)s
"""
# A number added again moves: its agent, channel, world and account are the newest said.
PUT = """
INSERT INTO routes (org, number, agent, channel, env, managed, account, networks)
VALUES (%(org)s, %(number)s, %(agent)s, %(channel)s, %(env)s, %(managed)s, %(account)s,
        %(networks)s)
ON CONFLICT (org, number) DO UPDATE SET agent = excluded.agent, channel = excluded.channel,
    env = excluded.env, managed = excluded.managed, account = excluded.account,
    networks = excluded.networks
"""
REMOVE = "DELETE FROM routes WHERE org = %(org)s AND number = %(number)s RETURNING number"
MOVE = "UPDATE routes SET env = %(env)s WHERE org = %(org)s AND number = %(number)s RETURNING env"
MANAGED = "SELECT count(*) AS bought FROM routes WHERE org = %(org)s AND env = %(env)s AND managed"

NOT_A_ROOM_CONFIG = "room_config is not a LiveKit RoomConfiguration: {reason}"
NO_LIVEKIT = "this box has no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: it starts no call"
# Room names ride one request's query, so they are asked for in batches.
ROOMS_A_REQUEST = 100
# livekit's code for a room that is not there.
ROOM_GONE = "not_found"


# `trunk` names the carrier account the leg is dialled through; the worker asks the gateway for
# its inline configuration, so no secret rides a dispatch.
class Dialling(BaseModel):
    """The leg an outbound job places: trunk, far end, the number shown, the media's ceiling."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    trunk: str
    to: str
    shown: str
    max_duration_s: int = 0


class Handover(BaseModel):
    """A production ring the developer's own phone made, taken by their sandbox corner."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    holder: str
    fleet: str


# Written by the gateway alone, signed into a token or sent by the server API, so the worker
# trusts it: the corner a call runs in, and on an outbound call the ceiling of its leg.
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


async def of_org(pool: Pool, org: str, env: Env) -> list[Route]:
    """The org's numbers in one world, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_ORG, {"org": org, "env": env})).fetchall()
    return [_route(row) for row in rows]


async def at(pool: Pool, channel: Channel, number: str) -> Route | None:
    """The route a call dialled to this number takes, whatever org typed it."""
    asked = {"channel": channel, "number": number}
    async with pool.connection() as connection:
        rows = await (await connection.execute(AT, asked)).fetchall()
    if not rows:
        return None
    answering = _route(rows[0])
    for other in rows[1:]:
        logger.warning(TWO_ORGS, number, answering.org, other["org"])
    return answering


async def of_number(pool: Pool, org: str, number: str) -> Route | None:
    """The org's route at this number, whatever world it is in."""
    async with pool.connection() as connection:
        row = await (await connection.execute(OF_NUMBER, {"org": org, "number": number})).fetchone()
    return None if row is None else _route(row)


async def put(
    pool: Pool, route: Route, *, account: str | None, networks: Sequence[str] = ()
) -> None:
    """Keep the route, moving the number if the org had it: the account it lives in, if any."""
    row = {
        "org": route.org,
        "number": route.number,
        "agent": route.agent,
        "channel": route.channel,
        "env": route.env,
        "managed": route.managed,
        "account": account,
        "networks": list(networks),
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, row)


async def remove(pool: Pool, org: str, number: str) -> bool:
    """Forget the org's route at the number; whether there was one."""
    async with pool.connection() as connection:
        gone = await connection.execute(REMOVE, {"org": org, "number": number})
        return await gone.fetchone() is not None


async def moved(pool: Pool, org: str, number: str, env: Env) -> bool:
    """Move the org's number into the world; whether it had it."""
    async with pool.connection() as connection:
        done = await connection.execute(MOVE, {"org": org, "number": number, "env": env})
        return await done.fetchone() is not None


async def managed_in(pool: Pool, org: str, env: Env) -> int:
    """How many of the org's numbers in the world the box bought."""
    async with pool.connection() as connection:
        row = await (await connection.execute(MANAGED, {"org": org, "env": env})).fetchone()
    return 0 if row is None else int(row["bought"])


def written(dispatch: Dispatch) -> str:
    """The dispatch as a job's metadata: compact JSON, absent fields absent."""
    said = dispatch.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return json.dumps(said, separators=(",", ":"))


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


# livekit-api opens an HTTP session per client: one per process, closed with it.
def server_of(settings: Settings) -> api.LiveKitAPI:
    """The SFU's server API, on the box's key pair."""
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise NotAvailable(NO_LIVEKIT)
    return api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    )


def _route(row: DictRow) -> Route:
    return Route(
        org=row["org"],
        agent=row["agent"],
        channel=row["channel"],
        number=row["number"],
        env=row["env"],
        managed=row["managed"],
    )
