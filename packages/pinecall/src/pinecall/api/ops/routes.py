"""The doors onto the routes: the read a worker makes, and the three an operator makes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.api.deps import AppKeyDep, OrgsDep, RoutesDep, require_org
from pinecall.api.scope.operator_key import operators_router
from pinecall.api.scope.request_scope import CornerDep
from pinecall.auth.keys import is_fleet_key
from pinecall.auth.request_scope import NOT_YOUR_CORNER
from pinecall.types import PRODUCTION, Channel, Env, Route
from pinecall_protocol import WireModel

# The worker's own door, on the org's API key, exactly as every other door of the runtime.
router = APIRouter()

# Every /v1/ops door takes the operator key and nothing else, checked before the endpoint runs.
# It is the box's own key out of the environment, so it names no org and every door here does.
operator = operators_router()

# The hop carries the domain object itself: FastAPI serializes the Route dataclass from the door's
# annotation, and worker/wire.py's adapter validates it back. See docs/decisions/worker.md.

# Nothing to remove. 404 and not 204: `routes rm` on a number nobody typed is a typo, and a verb
# that answered yes to it would send the operator looking for the change somewhere else.
NO_SUCH_ROUTE = "no route for {number} in org {org}"

# The one query parameter every operator door takes, by id or by slug. Required: guessing an org
# would move a number inside somebody else's.
ORG = Query(description="whose routes: the org the agents register under, by id or slug")

# Which world's doors. Production unless the operator says: a number somebody bought rings the
# deployed agent, and typing one into the sandbox is the deliberate act.
ENV = Query(PRODUCTION, description="which world's doors: production (default) or sandbox")


class Wanted(WireModel):
    """What `routes add` sends: the number, the agent that answers it, the door, the world."""

    org: str
    number: str
    agent: str
    channel: Channel
    env: Env = PRODUCTION


class RouteAdded(WireModel):
    """What `routes add` is answered: the one row the number now is."""

    route: Route


# A tenant's key reads the doors of its own org and its own world, and is refused any other: a key
# that could ask for another org's routes would be a key that could route a call into somebody
# else's agent. The FLEET's key is the one exception, because the box's one worker answers every
# org's calls: it asks for the corner the dispatch named (`?org=&env=&holder=`), or — for a call
# on the box's own trunk, which names no org — for the one door that answers the number dialled
# (`?number=&channel=`), which the gateway finds across every org.
@router.get("/v1/routes")
async def routes(
    key: AppKeyDep,
    corner: CornerDep,
    table: RoutesDep,
    number: Annotated[str | None, Query(description="the fleet's: the number dialled")] = None,
    channel: Annotated[Channel | None, Query(description="the fleet's: its channel")] = None,
) -> list[Route]:
    """The doors of one corner, or the one door a number rings, so a job is resolved at once."""
    if number is not None or channel is not None:
        if not is_fleet_key(key) or number is None or channel is None:
            raise HTTPException(403, NOT_YOUR_CORNER)
        door = await table.at(channel, number)
        return [] if door is None else [door]
    return list(await table.of_org(corner.org, corner.env))


@operator.get("/routes")
async def list_routes(
    table: RoutesDep, orgs: OrgsDep, org: str = ORG, env: Env = ENV
) -> list[Route]:
    """The same doors the worker is given: every row this org answers at in this world."""
    owner = await require_org(org, orgs)
    return list(await table.of_org(owner.id, env))


# Nothing is ever taken from anybody any more: a door was a row or a class's declaration, and the
# row outranked the declaration, so `add` said whose door it had just moved. A class declares none
# now, so a number moves from one row to another and the row itself is the whole answer.
@operator.post("/routes")
async def add(said: Wanted, table: RoutesDep, orgs: OrgsDep) -> RouteAdded:
    """Add the number or move it. One row per number per org: this is an upsert."""
    route = _a_route(said, (await require_org(said.org, orgs)).id)
    await table.put(route)
    return RouteAdded(route=route)


@operator.delete("/routes/{number}", status_code=HTTP_204_NO_CONTENT)
async def remove(number: str, table: RoutesDep, orgs: OrgsDep, org: str = ORG) -> None:
    """Forget the number. A number nobody typed is a 404, so a typo is never a quiet success."""
    owner = await require_org(org, orgs)
    if not await table.remove(owner.id, number):
        raise HTTPException(404, NO_SUCH_ROUTE.format(number=number, org=owner.slug))


def _a_route(said: Wanted, org: str) -> Route:
    """The domain's own Route, so a number that is not a number is refused before it is stored."""
    return Route(org=org, agent=said.agent, channel=said.channel, number=said.number, env=said.env)
