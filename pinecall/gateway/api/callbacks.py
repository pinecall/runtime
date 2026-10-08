"""Callbacks: somebody the overflow told to wait, and the org's list of them."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pinecall.domain.errors import (
    DeclarationRefused,
    NotFound,
)
from pinecall.domain.person import THE_FLEET
from pinecall.gateway import _deps
from pinecall.gateway._deps import (
    Acting,
    CallsKey,
    GatewayDep,
    WorkerKey,
)
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.tenancy import keys
from pinecall.wire.events import CallbackRequested
from pinecall.wire.rest.calls import CallbackList, CallbackRequest, CallbackRow

router = APIRouter()


NOT_THIS_ORGS = "agent {agent} is not this org's"


NAME_THE_CALL = "the fleet's key asks a call back for the call it serves: name the call"


CALLBACK = "callback.requested"


A_PAGE = 500


class CallbackQuery(BaseModel):
    """Where the org's list of callbacks continues, and whose."""

    after: int = 0
    agent: str | None = None


# The overflow names the call it answered, and the agent must be that call's org's; an app
# names its own org's agent.
@router.post("/v1/callbacks", status_code=204)
async def request_callback(body: CallbackRequest, key: WorkerKey, gateway: GatewayDep) -> None:
    """Somebody the overflow told to wait for a call back, on the agent's log."""
    keys.check_agent(key.bearer, body.agent)
    owner = await gateway.logs.store.owner(body.agent)
    fleet = THE_FLEET in key.bearer.key.scopes
    acting_for = await _org_of_the_call(gateway, key, body.call) if fleet else key.org
    if owner is None or owner != acting_for:
        raise NotFound(NOT_THIS_ORGS.format(agent=body.agent))
    wanted = CallbackRequested(
        channel=body.channel, number=body.number, via="overflow", call=body.call, contact=None
    )
    await gateway.logs.agent(body.agent).append(CALLBACK, wanted.written())


@router.get("/v1/callbacks", response_model_exclude_unset=True)
async def list_callbacks(
    key: CallsKey, gateway: GatewayDep, query: Annotated[CallbackQuery, Query()]
) -> CallbackList:
    """The org's callbacks in the key's world, oldest first, a page at a time."""
    page = await gateway.logs.store.across([CALLBACK], after=query.after, limit=A_PAGE)
    # An agent's log holds both worlds' callbacks: each is the world of the call it names.
    named = [str(item.entry.data["call"]) for item in page if item.entry.data.get("call")]
    worlds = await queries.envs_of_calls(gateway.connections.pool, named)
    ours = [
        item
        for item in page
        if item.org == key.org
        and worlds.get(str(item.entry.data.get("call"))) == key.env
        and (query.agent is None or item.entry.agent == query.agent)
    ]
    return CallbackList(
        requests=[
            CallbackRow.model_validate(
                {
                    "position": our.position,
                    "agent": our.entry.agent,
                    "ts": our.entry.ts,
                    **our.entry.data,
                }
            )
            for our in ours
        ],
        next=page[-1].position if len(page) == A_PAGE else None,
    )


async def _org_of_the_call(gateway: Gateway, key: Acting, call: str | None) -> str:
    if call is None:
        raise DeclarationRefused(NAME_THE_CALL)
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None or kept.scope is None:
        raise NotFound(_deps.NEVER_OPENED.format(call=call))
    if kept.scope.env != key.env:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    return kept.scope.org
