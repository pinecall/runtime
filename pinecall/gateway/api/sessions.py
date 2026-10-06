"""The lists of calls: an agent's newest and the org's, one row each, as the console draws them."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from pinecall.domain.errors import NotAllowed
from pinecall.gateway import _deps
from pinecall.gateway._deps import GatewayDep, Reader, ReaderDep
from pinecall.gateway._gateway import Gateway
from pinecall.log import lists, queries
from pinecall.log.readers import project_state
from pinecall.wire.rest.calls import CallList, CallRow, SessionScore

router = APIRouter()


A_SCREENFUL = 20


LINE_FROM_STATE = (
    "status",
    "channel",
    "direction",
    "to",
    "caller",
    "started_at",
    "ended_at",
    "end_reason",
    "outcome",
    "cost",
    "attention",
)


class ListQuery(BaseModel):
    """What a list of calls asks for: an agent, words, a channel, a page below a call."""

    limit: int = Field(A_SCREENFUL, ge=1, le=_deps.LONGEST_LIST)
    q: str | None = Field(None, max_length=200)
    agent: str | None = None
    channel: str | None = None
    before: str | None = None


@router.get("/v1/agents/{slug}/sessions")
async def list_agent_calls(
    slug: str, reading: ReaderDep, gateway: GatewayDep, query: Annotated[ListQuery, Query()]
) -> CallList:
    """The agent's newest calls, one row each."""
    wanted = lists.ListFilters(
        agent=slug, q=query.q or None, channel=query.channel, before=query.before
    )
    return await _sessions(gateway, reading, wanted, query.limit)


@router.get("/v1/sessions")
async def list_calls(
    reading: ReaderDep, gateway: GatewayDep, query: Annotated[ListQuery, Query()]
) -> CallList:
    """The org's newest calls across its agents, one row each."""
    wanted = lists.ListFilters(
        agent=query.agent or None, q=query.q or None, channel=query.channel, before=query.before
    )
    return await _sessions(gateway, reading, wanted, query.limit)


async def _sessions(
    gateway: Gateway, reading: Reader, wanted: lists.ListFilters, limit: int
) -> CallList:
    if reading.acting is None or reading.scope is None:
        raise NotAllowed("a list of calls is read with a key, not a call's token")
    found = await lists.found(gateway.connections.pool, reading.scope, wanted, limit=limit)
    facts = await queries.facts_of_calls(gateway.connections.pool, found.calls)
    lines: list[CallRow] = []
    for call in found.calls:
        state = await gateway.logs.reading(call).snapshot()
        if state.seq == 0:
            continue
        text = project_state(state, "tenant", gateway.sockets.declared(state.agent or ""))
        fact = facts.get(call)
        score = None
        if fact is not None and fact.judged is not None and fact.passed is not None:
            score = SessionScore(
                held=fact.held or 0, judged=fact.judged, passed=fact.passed, reason=fact.reason
            )
        lines.append(
            CallRow.model_validate(
                {
                    **{name: text.get(name) for name in LINE_FROM_STATE},
                    "from": text.get("from"),
                    "call": call,
                    "agent": state.agent or "",
                    "last_seq": state.seq,
                    "live": not await gateway.logs.store.sealed(call),
                    "score": None if score is None else score.written(),
                    "flags": [] if fact is None else fact.flags,
                }
            )
        )
    return CallList(calls=lines, total=found.total, next=found.next)
