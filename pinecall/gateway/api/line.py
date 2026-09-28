"""The developer's line: whose terminal a ring at the agent lands in, and their phones."""

from typing import Annotated

from fastapi import APIRouter, Query

from pinecall.channels import routes
from pinecall.domain.errors import (
    Conflict,
    NotAllowed,
)
from pinecall.domain.names import SANDBOX, parse_e164
from pinecall.domain.scope import Scope
from pinecall.fleet import worlds
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting, AppKey, CallsKey, FleetKey, GatewayDep, ScopeDep
from pinecall.gateway._state import Gateway
from pinecall.wire.rest.agents import (
    DeveloperPhones,
    RingHandoff,
    RingTarget,
    ScopeHolder,
    TestNumber,
    TestNumbers,
)

router = APIRouter()


NOT_IN_PRODUCTION = "production has one corner and the org holds it: there is nothing to route"

NOBODY_TO_ROUTE_TO = "a phone reaches a person's own corner, and this key names no person"


@router.get("/v1/agents/{slug}/line")
async def get_line(slug: str, _key: CallsKey, where: ScopeDep, gateway: GatewayDep) -> RingTarget:
    """Who holds the agent's line, and who else could take it."""
    return await _line(gateway, where, slug)


@router.post("/v1/agents/{slug}/line")
async def take_line(slug: str, _key: AppKey, where: ScopeDep, gateway: GatewayDep) -> RingTarget:
    """Take the agent's line for this scope."""
    gateway.sockets.take_the_line(where, slug)
    return await _line(gateway, where, slug)


@router.delete("/v1/agents/{slug}/line")
async def drop_line(slug: str, _key: AppKey, where: ScopeDep, gateway: GatewayDep) -> RingTarget:
    """Let the line go, to the newest other scope that could take it."""
    gateway.sockets.drop_the_line(where, slug)
    return await _line(gateway, where, slug)


@router.put("/v1/line/from")
async def put_developer_phones(
    developer_phones: DeveloperPhones, key: AppKey, gateway: GatewayDep
) -> dict[str, list[str]]:
    """Send rings from this phone to the key's person's own scope."""
    holder = _person_of(key)
    gateway.sockets.calls_from(key.env, parse_e164(developer_phones.number), holder)
    return {"calling": gateway.sockets.calling(key.env, holder)}


@router.delete("/v1/line/from")
async def drop_developer_phones(key: AppKey, gateway: GatewayDep) -> dict[str, list[str]]:
    """Stop sending this person's phones to their scope, and say which were forgotten."""
    return {"forgot": gateway.sockets.forget_calls_from(key.env, _person_of(key))}


# A developer lacks the numbers scope in production: this shows production's numbers and agents.
@router.get("/v1/line/numbers")
async def list_test_numbers(key: AppKey, gateway: GatewayDep) -> TestNumbers:
    """The production numbers a developer's phone can dial, and the phones that are theirs."""
    holder = _person_of(key)
    doors = await routes.of_org(gateway.connections.pool, key.org, "production")
    return TestNumbers(
        calling=gateway.sockets.calling(key.env, holder),
        numbers=[
            TestNumber(number=door.number, agent=door.agent)
            for door in doors
            if door.channel == "phone" and door.number is not None
        ],
    )


# A developer's own phone dialling the production number reaches their sandbox copy, while they
# hold it; every other caller reaches production.
@router.get("/v1/agents/{slug}/rings-for")
async def rings_for(
    slug: str,
    _key: FleetKey,
    gateway: GatewayDep,
    org: Annotated[str, Query()],
    caller: Annotated[str, Query()],
) -> RingHandoff:
    """Where a production ring from this phone goes: a developer's scope and fleet, or nowhere."""
    taking = gateway.sockets.taking(Scope(org, SANDBOX), slug, caller)
    if taking is None or not taking.scope.holder:
        return RingHandoff()
    if caller not in gateway.sockets.calling(SANDBOX, taking.scope.holder):
        return RingHandoff()
    fleet = worlds.fleet_of(await worlds.fleets(gateway.connections.pool), SANDBOX)
    return RingHandoff(holder=taking.scope.holder, fleet=fleet)


def _person_of(key: Acting) -> str:
    if key.env != SANDBOX:
        raise Conflict(NOT_IN_PRODUCTION)
    if key.bearer.member is None:
        raise NotAllowed(NOBODY_TO_ROUTE_TO)
    return key.bearer.member.id


async def _line(gateway: Gateway, where: Scope, slug: str) -> RingTarget:
    holder = gateway.sockets.line_of(where.env, slug)
    waiting = [
        item
        for item in gateway.sockets.waiting_for_the_line(where, slug)
        if item.scope.holder != holder
    ]
    holding = None
    if holder is not None:
        holding = await _deps.named_holder(
            gateway, Scope(where.org, where.env, holder)
        ) or ScopeHolder(holder=None, name=None)
    return RingTarget(
        agent=slug,
        env=where.env,
        held=holder is not None,
        holding=holding,
        # In production every holder is the org's own: the line is never "yours" there.
        yours=holder is not None and holder == where.holder and where.env == SANDBOX,
        waiting=[
            await _deps.named_holder(gateway, waiter.scope) or ScopeHolder(holder=None, name=None)
            for waiter in waiting
        ],
        calling=gateway.sockets.calling(where.env, where.holder) if where.holder else [],
    )
