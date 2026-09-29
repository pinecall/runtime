"""Callbacks: somebody the overflow told to wait, and the org's list of them."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pinecall.domain.errors import (
    NotFound,
)
from pinecall.domain.person import THE_FLEET
from pinecall.gateway._deps import (
    CallsKey,
    GatewayDep,
    WorkerKey,
)
from pinecall.wire.events import CallbackRequested
from pinecall.wire.rest.calls import CallbackList, CallbackRequest, CallbackRow

router = APIRouter()


NOT_THIS_ORGS = "agent {agent} is not this org's"


CALLBACK = "callback.requested"


A_PAGE = 500


class CallbackQuery(BaseModel):
    """Where the org's list of callbacks continues, and whose."""

    after: int = 0
    agent: str | None = None


# The overflow names any org's agent; an app, only its own.
@router.post("/v1/callbacks", status_code=204)
async def request_callback(body: CallbackRequest, key: WorkerKey, gateway: GatewayDep) -> None:
    """Somebody the overflow told to wait for a call back, on the agent's log."""
    owner = await gateway.logs.store.owner(body.agent)
    if owner is None or (owner != key.org and THE_FLEET not in key.bearer.key.scopes):
        raise NotFound(NOT_THIS_ORGS.format(agent=body.agent))
    wanted = CallbackRequested(
        channel=body.channel, number=body.number, via="overflow", call=body.call, contact=None
    )
    await gateway.logs.agent(body.agent).append(CALLBACK, wanted.written())


@router.get("/v1/callbacks", response_model_exclude_unset=True)
async def list_callbacks(
    key: CallsKey, gateway: GatewayDep, query: Annotated[CallbackQuery, Query()]
) -> CallbackList:
    """The org's callbacks, oldest first, a page at a time."""
    page = await gateway.logs.store.across([CALLBACK], after=query.after, limit=A_PAGE)
    ours = [
        item
        for item in page
        if item.org == key.org and (query.agent is None or item.entry.agent == query.agent)
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
