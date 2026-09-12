"""The doors onto the routes: the read a worker makes, and the three an operator makes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter

from pinecall.api._deps import AppKeyDep, OrgsDep, RoutesDep, an_org
from pinecall.api._operator import an_operator
from pinecall.api.agents.registry import RegistryDep
from pinecall.auth.keys import held_by
from pinecall.routes import answering
from pinecall.types import PRODUCTION, Channel, DeclarationRefused, Env, Route
from pinecall_protocol import WireModel

# The worker's own door, on the org's API key, exactly as every other door of the runtime.
router = APIRouter()

# Every /v1/ops door takes the operator key and nothing else, checked before the endpoint runs.
# It is the box's own key out of the environment, so it names no org and every door here does.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

# The hop carries the domain object itself, adapted by pydantic — the same adapter
# worker/client.py validates it back through. See docs/decisions/worker.md.
ROUTES: TypeAdapter[tuple[Route, ...]] = TypeAdapter(tuple[Route, ...])
ANSWERING: TypeAdapter[tuple[answering.Answering, ...]] = TypeAdapter(
    tuple[answering.Answering, ...]
)

# A removed route has nothing to say back. cli/operator.py names the same number on its own
# side, because the CLI may not import the gateway: they are two processes, as often as two boxes.
NO_BODY = 204

# Nothing to remove. 404 and not 204: `routes rm` on a number nobody typed is a typo, and a verb
# that answered yes to it would send the operator looking for the change somewhere else.
NO_SUCH_ROUTE = "no route for {number} in org {org}"

# The one query parameter every operator door takes, by id or by slug. Required: guessing an org
# would move a number inside somebody else's.
ORG = Query(description="whose routes: the org the agents register under, by id or slug")

# Which world's doors. Production unless the operator says: a number somebody bought rings the
# deployed agent, and typing one into development is the deliberate act.
ENV = Query(PRODUCTION, description="which world's doors: production (default) or development")


class Wanted(WireModel):
    """What `routes add` sends: the number, the agent that answers it, the door, the world."""

    org: str
    number: str
    agent: str
    channel: Channel
    env: Env = PRODUCTION


# The org is the key's here, never a query parameter: a worker reads the doors of the org whose
# key it holds, and a key that could ask for another org's routes would be a key that could route
# a call into somebody else's agent.
@router.get("/v1/routes")
async def routes(key: AppKeyDep, registry: RegistryDep, table: RoutesDep) -> list[dict[str, Any]]:
    """Every door the org answers in the key's world, so a job is resolved without asking again."""
    answered = await answering.answered(key.org, key.env, registry, table, held_by(key))
    return list(ROUTES.dump_python(tuple(door.route for door in answered), mode="json"))


@operator.get("/routes")
async def listed(
    registry: RegistryDep, table: RoutesDep, orgs: OrgsDep, org: str = ORG, env: Env = ENV
) -> list[dict[str, Any]]:
    """The same doors the worker is given, each saying which of the two tables put it there."""
    owner = await an_org(org, orgs)
    return list(
        ANSWERING.dump_python(await answering.answered(owner.id, env, registry, table), mode="json")
    )


@operator.post("/routes")
async def add(
    said: Wanted, registry: RegistryDep, table: RoutesDep, orgs: OrgsDep
) -> dict[str, Any]:
    """Add the number or move it, and say which agent it was taken from, if it was taken."""
    route = _a_route(said, (await an_org(said.org, orgs)).id)
    await table.put(route)
    taken_from = [
        lost.agent
        for _, lost in answering.overridden([route], registry.routes(route.org, route.env))
    ]
    return {"route": _as_json(route), "overrides": taken_from[0] if taken_from else None}


@operator.delete("/routes/{number}", status_code=NO_BODY)
async def remove(number: str, table: RoutesDep, orgs: OrgsDep, org: str = ORG) -> None:
    """Forget the number. A number nobody typed is a 404, so a typo is never a quiet success."""
    owner = await an_org(org, orgs)
    if not await table.remove(owner.id, number):
        raise HTTPException(404, NO_SUCH_ROUTE.format(number=number, org=owner.slug))


def _as_json(route: Route) -> dict[str, Any]:
    """One route as the wire says it, through the adapter both sides of the hop already share."""
    return cast("dict[str, Any]", ROUTES.dump_python((route,), mode="json")[0])


def _a_route(said: Wanted, org: str) -> Route:
    """The domain's own Route, so a number that is not a number is refused before it is stored."""
    try:
        return Route(
            org=org, agent=said.agent, channel=said.channel, number=said.number, env=said.env
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
