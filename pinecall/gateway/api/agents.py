"""What a worker asks about an agent, and the agents and routes the org holds."""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from pinecall.channels import routes
from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import Route
from pinecall.domain.errors import (
    NotFound,
)
from pinecall.domain.names import THE_WIDGET, Channel
from pinecall.domain.person import THE_FLEET
from pinecall.domain.scope import Scope
from pinecall.gateway import _deps
from pinecall.gateway._call_setup import keys_of, tuned
from pinecall.gateway._deps import CallsKey, DispatchedDep, GatewayDep, ScopeDep, WorkerKey
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import NO_AGENT, Registration
from pinecall.providers import catalog
from pinecall.providers.credentials import Pipeline, pipeline
from pinecall.tenancy import agents, keys
from pinecall.wire.rest.agents import (
    AgentList,
    AgentRow,
    HoldAudio,
)

router = APIRouter()


# Every agent answers on the widget; no route row is needed for it.
ON_THE_WEB: frozenset[Channel] = frozenset({THE_WIDGET})


class RouteQuery(BaseModel):
    """A worker asking for the route a number rings, across every org."""

    number: str | None = None
    channel: Channel = "phone"


# A worker names the call it asks for before opening it (`for_call`: `call` would ask to act in an
# opened call's scope), so a canary's share is picked by the call's id; a worker that names none
# (an older one) is answered the version every call off the canary runs.
@router.get("/v1/agents/{slug}/config")
async def agent_config(
    slug: str,
    _key: _deps.DeclarationKey,
    where: ScopeDep,
    gateway: GatewayDep,
    for_call: Annotated[str | None, Query()] = None,
) -> AgentConfig:
    """The agent as the scope runs it, for the call named: its declaration under the settings."""
    found = _registration_of(gateway, where, slug)
    configured = await catalog.providers(gateway.connections.pool)
    tuned_config, _ = await tuned(
        gateway.connections.pool, found.config, where, configured, call=for_call
    )
    return tuned_config


# The one answer that carries keys: to the fleet's key, or the org's own worker's.
@router.get("/v1/agents/{slug}/provider-keys")
async def agent_credentials(
    slug: str,
    _key: WorkerKey,
    where: ScopeDep,
    gateway: GatewayDep,
    for_call: Annotated[str | None, Query()] = None,
) -> Pipeline:
    """The three stages a call of the agent runs, each on the key it runs on."""
    found = _registration_of(gateway, where, slug)
    configured = await catalog.providers(gateway.connections.pool)
    tuned_config, _ = await tuned(
        gateway.connections.pool, found.config, where, configured, call=for_call
    )
    return pipeline(
        tuned_config,
        configured,
        await keys_of(gateway.connections.pool, gateway.connections.vault, where),
    )


@router.get("/v1/agents/{slug}/hold-audio")
async def hold_audio(slug: str, _key: WorkerKey, where: ScopeDep, gateway: GatewayDep) -> HoldAudio:
    """What a caller of the agent hears while a tool runs."""
    chosen = await agents.hold_of(gateway.connections.pool, where, slug)
    if chosen is None:
        return HoldAudio(played="default")
    return HoldAudio(
        played=chosen.played, sha256=chosen.sha256, seconds=chosen.seconds, name=chosen.name
    )


@router.get("/v1/agents/{slug}/hold-audio/audio")
async def hold_clip(slug: str, _key: WorkerKey, where: ScopeDep, gateway: GatewayDep) -> Response:
    """The org's own clip, Ogg Opus."""
    audio = await agents.hold_audio(gateway.connections.pool, where, slug)
    if audio is None:
        raise NotFound(f"agent {slug} plays no clip of its own")
    return Response(audio, media_type="audio/ogg")


# A route the operator adds answers the next job: nothing is cached.
@router.get("/v1/routes")
async def list_routes(
    key: WorkerKey,
    named: DispatchedDep,
    gateway: GatewayDep,
    query: Annotated[RouteQuery, Query()],
) -> list[Route]:
    """The routes of a scope; for the fleet's key and a number, the route that number rings."""
    fleet = THE_FLEET in key.bearer.key.scopes
    if fleet and query.number is not None:
        found = await routes.at(gateway.connections.pool, query.channel, query.number)
        return [] if found is None or found.env != key.bearer.key.env else [found]
    where = named if fleet and named is not None else keys.scope_of(key.bearer, key.env)
    return await routes.of_org(gateway.connections.pool, where.org, where.env)


@router.get("/v1/agents")
async def list_agents(key: CallsKey, where: ScopeDep, gateway: GatewayDep) -> AgentList:
    """The org's held agents in the world: one row per slug, one per scope for a team reader."""
    every = _deps.sees_every_scope(key)
    doors: dict[str, set[Channel]] = {}
    for route in await routes.of_org(gateway.connections.pool, where.org, where.env):
        doors.setdefault(route.agent, set()).add(route.channel)
    listed: list[AgentRow] = []
    for registration in gateway.sockets.holding(where, every_corner=every):
        channels = sorted(doors.get(registration.slug, set[Channel]()) | ON_THE_WEB)
        holder = await _deps.named_holder(gateway, registration.scope)
        listed.append(AgentRow(slug=registration.slug, channels=channels, holder=holder))
    return AgentList(agents=listed)


# Another org's agent is the same 404 as nobody's: its existence does not leak.
def _registration_of(gateway: Gateway, where: Scope, slug: str) -> Registration:
    registration = gateway.sockets.of(where, slug)
    if registration is None or registration.scope.org != where.org:
        raise NotFound(NO_AGENT.format(slug=slug))
    return registration
