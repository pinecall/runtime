"""A call served here, as a door reaches it: its socket, set up, first seen, looked up, claimed."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import Literal

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.domain.names import CHANNELS_WITH_A_NUMBER, JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._served import Served, Serving
from pinecall.gateway._sockets import Registration, SocketId, Sockets, orgs_own
from pinecall.log import queries
from pinecall.log.logs import Log, arrival_entry
from pinecall.log.private import Privacy
from pinecall.retrieval import lookups
from pinecall.retrieval.lookups import OnTheCall
from pinecall.session.tools import ToolCalls
from pinecall.tenancy import admission
from pinecall.tenancy.codes import Codes
from pinecall.wire.events import CallClaimed
from pinecall.wire.rest.calls import LookupRequest


# A call opened with a key goes to that key's scope; one that rang, to its phone owner, or the line.
def serving_agent(
    sockets: Sockets, scope: Scope, agent: str, app: SocketId | None, context: CallContext
) -> Registration | None:
    """The socket a new call of the agent reaches."""
    if app is None and context.route.channel in CHANNELS_WITH_A_NUMBER:
        return sockets.taking(orgs_own(scope), agent, context.caller)
    return sockets.serving(scope, agent, app)


def served_call(
    serving: Serving,
    owner: SocketId | None,
    context: CallContext,
    config: AgentConfig,
    scope: Scope,
) -> Served:
    """Serve the call to its socket, or park it for the next one; before its first entry."""
    log = serving.logs.writing(context.call, config.slug)
    log.privacy = Privacy(serving.connections.vault, config)
    served = Served(
        agent=config.slug,
        scope=scope,
        app=owner,
        log=log,
        commands=asyncio.Queue(),
        context=context,
        config=config,
        tools=ToolCalls(
            config,
            log,
            partial(queries.tool_answered, serving.connections.pool, context.call),
            partial(queries.tool_called, serving.connections.pool, context.call),
        ),
    )
    serving.live.serve(served)
    return serving.live.calls[context.call]


# Parked and not counted: the socket holding its agent and the quota are its opener's.
def first_seen(
    serving: Serving, context: CallContext, config: AgentConfig, scope: Scope, now: float
) -> Served:
    """Serve a call another gateway opened, from what was kept when it opened."""
    serving.live.idle(now)
    served = served_call(serving, None, context, config, scope)
    served = replace(served, opened_here=False)
    serving.live.calls[served.call] = served
    serving.live.seen[served.call] = now
    return served


async def opened(log: Log, context: CallContext, agent: str) -> None:
    """call.ringing for an inbound call; an outbound one has its call.dialing already."""
    if context.direction == "outbound":
        return
    kind, data = arrival_entry(context, context.route.number or agent)
    await log.append(kind, data)


# The quotas are read per lookup, so a plan changed mid-call applies from the next turn.
async def looked_up(serving: Serving, served: Served, request: LookupRequest) -> JsonObject:
    """Recall or search for a call served here, written on its log, answered as the model reads."""
    pool = serving.connections.pool
    quotas = await admission.quotas_of(pool, served.scope.org, served.scope.env)
    on_the_call = OnTheCall(
        scope=served.scope,
        context=served.context,
        config=served.config,
        log=served.log,
        now=now_of(serving),
    )
    return await lookups.lookup(pool, serving.embedder, on_the_call, request, quotas=quotas)


async def claim_code(
    codes: Codes, served: Served, code: str, *, via: Literal["keypad", "agent"]
) -> bool:
    """Tie the call to the page showing the code; False when no page waits on it."""
    issued = await codes.claim(served.scope.env, served.agent, code, served.call)
    if issued is None:
        return False
    await served.log.append("call.claimed", CallClaimed(code=code, via=via).written())
    return True


def now_of(serving: Serving) -> datetime:
    """This moment by the store's clock, the one every entry of a call is stamped with."""
    return datetime.fromtimestamp(serving.logs.store.clock(), UTC)
