"""What a worker and a console ask the registry about one agent: what it declared, and its line."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from pinecall.api.agents.registry import Registry, RegistryDep
from pinecall.api.agents.session_config import tuned_for
from pinecall.api.deps import (
    AppKeyDep,
    CallsKeyDep,
    DeclarationKeyDep,
    MembersDep,
    RoutesDep,
    SettingsDep,
    TuningDep,
)
from pinecall.api.ops.peers import ProductionDep, SandboxDep
from pinecall.api.scope.request_scope import CornerDep, HeldDep
from pinecall.auth.keys import KeyRecord, is_held_by, is_operator_key
from pinecall.auth.members import Members
from pinecall.auth.peers import PeerUnreachable, RingsFor
from pinecall.types import (
    SANDBOX,
    THE_WIDGET,
    AgentConfig,
    Channel,
    DeclarationRefused,
    Route,
    is_a_deployment,
    parse_e164,
)
from pinecall.types.dispatch import Handover
from pinecall_protocol import WireModel
from pinecall_protocol.rest import AgentList, HeldAgent, LineHolder, TheLine

router = APIRouter()
logger = logging.getLogger(__name__)


# Whose declaration is the corner's: the key's own for a tenant, and for the fleet's key the
# corner of the call it is building a session for — the org, the world and the holder the
# dispatch named — which is how one worker serves a developer's sandbox copy and the org's
# production one from the same process. A slug that org does not hold is a 404 either way.
@router.get("/v1/agents/{slug}/config")
async def config(
    slug: str,
    key: DeclarationKeyDep,  # noqa: ARG001 — the scope is asked here; the corner says where
    corner: CornerDep,
    held: HeldDep,
    kept: TuningDep,
) -> AgentConfig:
    """What the app declared about this agent, resolved: the session is built from it, and the
    console draws the state by it."""
    # The corner's tuning is laid on through tuned_for(), the one resolving function every door
    # that builds a session calls, so what the org set arrives by the path a declaration travels.
    # The hop carries the domain object itself, serialized by pydantic off the annotation — the
    # adapter in worker/wire.py validates it back through. See docs/decisions/worker.md.
    resolved = await tuned_for(kept, corner.org, corner.env, corner.holder, slug, held.config)
    return resolved.config


# The console's first question, before it knows an agent to open: which agents are there. It is
# the live table and not the store, because an agent that no socket holds answers no call — the
# durable history of one is its own log, which the console already reads by slug. The envelope is
# the protocol's (protocol/schema/rest.json), so the console parses it with a generated schema.
@router.get("/v1/agents")
async def agents(
    key: CallsKeyDep, registry: RegistryDep, members: MembersDep, table: RoutesDep
) -> AgentList:
    """The org's agents in the key's world, by slug, in the order their sockets claimed them."""
    # Whose copies are listed is the key's own answer: a key that opens `team` — an admin's, the
    # operator's — sees every member's sandbox corner, and every row says whose it is. A developer
    # sees their corner and the org's, which is what they can open anyway.
    held = registry.holding(key.org, key.env, is_held_by(key), every_corner=is_operator_key(key))
    # The doors each one answers, which is a fact about the org's table and not about the class:
    # every agent is on the web, and a number is a row somebody typed (api/telephony/numbers.py).
    doors = _doors_of(await table.of_org(key.org, key.env))
    return AgentList(
        agents=[
            HeldAgent(
                slug=one.slug,
                channels=sorted(doors[one.slug] | ON_THE_WEB),
                holder=None
                if one.holder is None
                else await named_holder(key.org, one.holder, members),
            )
            for one in held
        ]
    )


# ── the line ────────────────────────────────────────────────────────────────────

# An org shares ONE sandbox number, so a call at it rings in one terminal. Which one is
# claimed and said out loud — before the line, the second `pinecall start` silently took the first
# one's calls and a developer dialling to test was answered in a colleague's scrollback. Alone,
# nobody claims anything: the first corner to hold an agent answers its ring. See
# api/agents/dial_in.py for the table, and docs/decisions/dispatch.md for why a number is shared.


@router.get("/v1/agents/{slug}/line")
async def the_line(
    slug: str, key: CallsKeyDep, registry: RegistryDep, members: MembersDep
) -> TheLine:
    """Whose terminal a ring at this agent's doors lands in, and who else could take it."""
    return await _said(slug, key, registry, members)


@router.post("/v1/agents/{slug}/line")
async def claim_the_line(
    slug: str, key: AppKeyDep, registry: RegistryDep, members: MembersDep
) -> TheLine:
    """Take this agent's ringing doors for this key's corner, whoever had them."""
    try:
        registry.take_the_line(key.env, slug, is_held_by(key))
    except DeclarationRefused as refused:
        raise HTTPException(409, str(refused)) from refused
    return await _said(slug, key, registry, members)


@router.delete("/v1/agents/{slug}/line")
async def drop_the_line(
    slug: str, key: AppKeyDep, registry: RegistryDep, members: MembersDep
) -> TheLine:
    """Stop answering the ring. Whoever else is still holding the agent picks it up."""
    registry.drop_the_line(key.env, slug, is_held_by(key))
    return await _said(slug, key, registry, members)


# A number is one PERSON's phone, so only a key that names one may say so. An org's own key —
# CI's, the box's — names nobody, and production has no corners to route between: there is one,
# and every ring lands in it.
NOBODY_TO_ROUTE_TO = "a number reaches a person's own corner, and this key names no person"
NOT_IN_PRODUCTION = "production has one corner and the box holds it: there is nothing to route"


class Calling(WireModel):
    """The phone a developer calls from, so their own calls reach their own agent."""

    number: str


@router.put("/v1/line/from")
async def calls_from(said: Calling, key: AppKeyDep, registry: RegistryDep) -> dict[str, list[str]]:
    """Every call this number makes reaches this key's corner, in whatever agent it is holding."""
    whose = _a_person(key)
    number = parse_e164(said.number)
    registry.calls_from(key.env, number, whose)
    return {"calling": list(registry.calling(key.env, whose))}


@router.delete("/v1/line/from")
async def forget_calls_from(key: AppKeyDep, registry: RegistryDep) -> dict[str, list[str]]:
    """This corner stops answering its own calls; they fall back to whoever holds the line."""
    return {"forgot": list(registry.forget_calls_from(key.env, _a_person(key)))}


# A developer tests on the numbers the customers call, so a developer has to be able to read them
# — and the numbers door will not tell them: it answers the key's own world, to a key that opens
# `numbers`, and theirs opens neither. This one answers production's phone numbers to whoever could
# be diverted from them, and nothing else about a route. They are production's rows, in
# production's database, so the sandbox asks production for them on the key it minted for it.
NO_PRODUCTION_TO_ASK = (
    "this sandbox holds no key of production, so it cannot read the numbers there: "
    "`pinecall-runtime box peer --from <production> --into <this sandbox>`"
)
NOT_ANSWERING = "production did not answer for its numbers: try again in a moment"


class NumberToCall(WireModel):
    """One of production's phone numbers, and the agent a call to it reaches."""

    number: str
    agent: str


class NumbersToCall(WireModel):
    """GET /v1/line/numbers: the phones this person dials from, and the numbers they can dial."""

    calling: list[str]
    numbers: list[NumberToCall]


@router.get("/v1/line/numbers")
async def numbers_to_call(
    key: AppKeyDep, registry: RegistryDep, production: ProductionDep
) -> NumbersToCall:
    """The org's production numbers and the agent each reaches, and the phones this person dials
    from: what a developer's phone dials, and whether the gateway knows that phone is theirs."""
    whose = _a_person(key)
    if production is None:
        raise HTTPException(503, NO_PRODUCTION_TO_ASK)
    try:
        typed = await production.production_routes_of(key.org)
    except PeerUnreachable as unreachable:
        logger.warning("line numbers: %s", unreachable)
        raise HTTPException(502, NOT_ANSWERING) from unreachable
    return NumbersToCall(
        calling=list(registry.calling(key.env, whose)),
        numbers=[
            NumberToCall(number=route.number, agent=route.agent)
            for route in typed
            if route.number is not None and route.channel == "phone"
        ],
    )


def _a_person(key: KeyRecord) -> str:
    """Whose corner this key opens, refusing the two keys that have none to route a call into."""
    if key.env != SANDBOX:
        raise HTTPException(409, NOT_IN_PRODUCTION)
    whose = is_held_by(key)
    if whose is None:
        raise HTTPException(403, NOBODY_TO_ROUTE_TO)
    return whose


async def _said(slug: str, key: KeyRecord, registry: Registry, members: Members) -> TheLine:
    """The line as a person reads it: whose it is by name, and every corner that could claim it."""
    whose = is_held_by(key)
    held = registry.has_a_line(key.env, slug)
    holder = registry.line_for(key.env, slug)
    waiting = [one for one in registry.waiting_for_the_line(key.env, slug) if one.holder != holder]
    return TheLine(
        agent=slug,
        env=key.env,
        held=held,
        holding=await named_holder(key.org, holder, members) if held else None,
        # PRODUCTION HAS NO CORNERS: `held_by` is None there for every key, and so is the line's
        # holder, so `None == None` made the line "yours" for anybody who asked — `pinecall line`
        # answered "rings in this terminal" on a laptop holding nothing, about a number ringing a
        # box (production, 2026-09-20). A line is somebody's only where corners exist.
        yours=held and holder == whose and not is_a_deployment(key.env),
        waiting=[await named_holder(key.org, one.holder, members) for one in waiting],
        calling=list(registry.calling(key.env, whose)),
    )


async def named_holder(org: str, holder: str | None, members: Members) -> LineHolder:
    """A corner, for a person. The org's own names nobody: a machine key — CI's — has no member."""
    if holder is None:
        return LineHolder(holder=None, name=None)
    member = await members.find(org, holder)
    return LineHolder(holder=holder, name=None if member is None else member.email)


# The worker asks this on every phone call to a production number, before it builds the session:
# is the phone dialling a developer's, who is holding this agent in the sandbox? The fleet's key
# names the org of the call (`?org=`); a tenant's key asks for its own. Nobody's is production's.
#
# The claims live where the developer's `pinecall start` and `pinecall line from` knock, which is
# the sandbox's gateway, so production asks its sandbox the same door on the key the sandbox minted
# for it when its own table has nothing. The answer names the fleet that builds the call — the
# instance holding the corner knows its own — and the worker hands the room to it. A sandbox that
# does not answer in time, or refuses, leaves the call where it rang: a customer's call is never
# held up by a developer's laptop.
@router.get("/v1/agents/{slug}/rings-for")
async def rings_for(
    slug: str,
    caller: Annotated[str, Query(description="the number dialling, as the SIP leg says it")],
    key: AppKeyDep,  # noqa: ARG001 — the scope is asked here; the corner says whose org
    corner: CornerDep,
    registry: RegistryDep,
    settings: SettingsDep,
    sandbox: SandboxDep,
) -> RingsFor:
    """Whose sandbox copy a production ring from this caller belongs to, or nobody's."""
    own = a_developers_own(registry, corner.org, slug, caller)
    if own is not None:
        return RingsFor.of(Handover(holder=own, fleet=settings.fleet))
    if sandbox is None:
        return RingsFor()
    try:
        return RingsFor.of(await sandbox.rings_for(slug, org=corner.org, caller=caller))
    except PeerUnreachable as unreachable:
        logger.warning("%s: the ring stays in production", unreachable)
        return RingsFor()


# A developer who said which phone is theirs (`pinecall line from`) and is holding this agent in the
# sandbox takes the calls their own phone makes to the real number: they test on the line the
# customers use, and every other caller still reaches production. Only that phone, only while they
# hold the agent, only in the org it is theirs in. `taking` is the sandbox's own answer for a ring
# from this caller — their corner, or the line — and it is theirs only when they claimed the phone.
def a_developers_own(registry: Registry, org: str, slug: str, caller: str) -> str | None:
    """The developer whose sandbox copy takes a production ring from this caller, or None."""
    held = registry.taking(SANDBOX, slug, caller)
    if held is None or held.holder is None or held.org != org:
        return None
    return held.holder if caller in registry.calling(SANDBOX, held.holder) else None


# Every agent answers the widget, whatever rows the org typed: the one door no table holds.
ON_THE_WEB: frozenset[Channel] = frozenset({THE_WIDGET})


def _doors_of(routes: Sequence[Route]) -> dict[str, set[Channel]]:
    """Every channel the org's rows give each agent, by slug: a listing reads them all at once."""
    doors: dict[str, set[Channel]] = defaultdict(set)
    for route in routes:
        doors[route.agent].add(route.channel)
    return doors
