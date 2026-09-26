"""The worker writing a call: open its log, append to it, seal it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.api.agents.registry import NO_UNCLAIMED, NOT_THAT_APP, RegistryDep
from pinecall.api.agents.session_config import tuned_for
from pinecall.api.calls.attachment import attach_socket
from pinecall.api.calls.deps import Serving, ServingDep
from pinecall.api.calls.opening import record_arrival, serving_agent
from pinecall.api.deps import (
    AdmissionDep,
    AppKeyDep,
    CallIndexDep,
    LogsDep,
    TokensDep,
    TuningDep,
)
from pinecall.api.live import LiveDep
from pinecall.auth.keys import KeyRecord, is_fleet_key, is_held_by
from pinecall.log.logs import CallLog
from pinecall.log.writers import Logs
from pinecall.tokens.spend import spent
from pinecall.types import AgentConfig, CallContext, Env
from pinecall_protocol import WireModel
from pinecall_protocol.registry import EVENTS

router = APIRouter()

# A worker may only write what the protocol declares. The data itself travels as the worker
# encoded it — the masker at the log's own door is what decides what is kept.
UNKNOWN_EVENT = "no event is called {type!r}: the log takes the protocol's own vocabulary"

# A worker whose key belongs to another org is not this org's worker, whatever it says it is
# running: a call it opened here would be written into somebody else's log.
NOT_THIS_ORG = "that call's route belongs to another org"

# The worker's key opens one world and the route it resolved is the other's: a key issued into
# the sandbox cannot open a production call, whatever number rang.
NOT_THIS_ENV = "this key opens {key}, and that call's route answers in {route}"

# Nothing was ever opened under this id here. 404, not 409: from the writer's side the call does
# not exist on this gateway at all, and the fix is to open it, not to retry.
NOT_OPEN = "this gateway is not writing call {call!r}: open it with POST /v1/calls first"

# A call whose head row sealed takes nothing more, and a worker still holding it is holding a call
# the reaper already closed: it has nothing to reopen.
SEALED = "call {call!r} is over: nothing more can be written to it"


class Opening(WireModel):
    """What a worker says when a call starts: whose agent it is, and everything it knows of it."""

    agent: str
    context: CallContext
    # The app socket this call claims — `pinecall talk` naming its own process, so a breakpoint
    # lands there and a real phone call does not. Without one the call takes the newest holder.
    app: str | None = None


class Appending(WireModel):
    """One entry as the worker hands it over: the wire's own type, and the event encoded."""

    type: str
    data: dict[str, Any]
    ephemeral: bool | None = None


# The log exists before the media does: a console holding an agent open sees the call arrive on
# the very fanout the worker will publish on. call.started stays the worker's to write — it is
# the moment the caller and the agent can hear each other, and only the session knows it.
@router.post("/v1/calls")
async def open_call(
    said: Opening,
    key: AppKeyDep,
    logs: LogsDep,
    registry: RegistryDep,
    live: ServingDep,
    tokens: TokensDep,
    admission: AdmissionDep,
    tuning: TuningDep,
) -> dict[str, int | None]:
    """A call started: its log open, the app's socket serving it, and the seconds it may last."""
    context = said.context
    org, env, holder = _whose_call(key, context)
    # A call a token opened is opened once: the second dispatch with the same token is refused
    # here, before a log exists for it, with the reason in the agent's own log.
    await spent(context, said.agent, tokens, logs)
    # The org's quotas, against the calls open here and what its log says it has consumed. The
    # refusal is in the agent's log before the worker hears the 429, and the sentence is the same.
    ceiling = await admission.a_call(org, said.agent, live.running(org))
    # Which process serves this call is asked here exactly as the chat door asks it, of the same
    # function: an app id that names no holder of this agent is refused, never quietly ignored.
    # Whose corner, though, depends on how the call ARRIVED. A number is the org's door and the
    # worker that dialled it holds a key naming nobody, so a ring lands on the LINE — nobody's
    # corner in production, and in the sandbox the developer who claimed it. Everything else was
    # opened BY a key holder, and lands in theirs. See api/agents/dial_in.py.
    serving = serving_agent(registry, env, said.agent, said.app, context, holder)
    if said.app is not None and serving is None:
        raise HTTPException(409, NOT_THAT_APP.format(app=said.app, slug=said.agent))
    # Held, but by consoles only: the phone call the flag keeps out of somebody's terminal. Refused
    # before the caller is greeted, not run with no app socket on it — a conversation whose every
    # tool goes out to nobody is worse than a line that drops. An app gone mid-setup still goes on.
    if serving is None and registry.of(env, said.agent, holder) is not None:
        raise HTTPException(409, NO_UNCLAIMED.format(slug=said.agent))
    # What the agent declared, resolved in the corner that serves it as the worker read it through
    # the config door, before the head row is claimed: the row records the versions the call ran on.
    held = serving or registry.of(env, said.agent, holder)
    corner = None if serving is None else serving.holder
    resolved = None
    if held:
        resolved = await tuned_for(tuning, org, env, corner, said.agent, held.config)
    config = AgentConfig(slug=said.agent) if resolved is None else resolved.config
    versions = None if resolved is None else resolved.versions
    await logs.owned(context.call, said.agent, org, env, holder, versions)
    log = logs.writing(context.call, said.agent)
    # Served before the first entry is written, so the app hears the call arrive: this is the very
    # same registration a text call gets, and it is what the call's tools travel down. What this
    # call recalls and searches is that corner's; one nobody is holding belongs to the org's own.
    app = serving.owner if serving is not None else None
    live.serve(
        context.call,
        said.agent,
        org,
        log,
        app,
        context=context,
        config=config,
        holder=corner,
    )
    await record_arrival(log, context, said.agent)
    # What is left of the org's minutes, for the worker to end this call at, and the quota they
    # come out of, for the credits.exhausted it writes when it does: null is no limit.
    if ceiling is None:
        return {"seconds_left": None, "minutes": None}
    return {"seconds_left": ceiling.seconds, "minutes": ceiling.minutes}


# A gateway that restarted forgot every call it was serving; the worker still holds each one, with
# the very context it opened it with. It says so here, and the call is served again as it was —
# no quota, no token, no call.ringing: the call was admitted once, and its log already says how it
# arrived. The socket holding the agent now is told with call.attached.
@router.post("/v1/calls/{call}/reopened", status_code=HTTP_204_NO_CONTENT)
async def reopen_call(
    call: str,
    said: Opening,
    key: AppKeyDep,
    logs: LogsDep,
    index: CallIndexDep,
    registry: RegistryDep,
    live: LiveDep,
    tuning: TuningDep,
) -> None:
    """A call this gateway forgot and the worker did not: served again from the worker's context."""
    context = said.context
    org, env, holder = _whose_call(key, context)
    if call != context.call:
        raise HTTPException(status_code=400, detail=f"the context is call {context.call!r}")
    if live.served(call) is not None:
        return
    corner = await index.corner_of_call(call)
    if corner is None:
        raise HTTPException(status_code=404, detail=NOT_OPEN.format(call=call))
    if corner.org != org:
        raise HTTPException(status_code=403, detail=NOT_THIS_ORG)
    if corner.sealed:
        raise HTTPException(status_code=409, detail=SEALED.format(call=call))
    serving = registry.serving(env, said.agent, None, holder)
    resolved = None
    if serving is not None:
        resolved = await tuned_for(tuning, org, env, serving.holder, said.agent, serving.config)
    config = AgentConfig(slug=said.agent) if resolved is None else resolved.config
    live.serve(
        call,
        said.agent,
        org,
        logs.writing(call, said.agent),
        None,
        context=context,
        config=config,
        holder=holder if serving is None else serving.holder,
    )
    if serving is not None:
        await attach_socket(live, call, serving.owner)


@router.post("/v1/calls/{call}/events", status_code=HTTP_204_NO_CONTENT)
async def append(
    call: str, said: Appending, key: AppKeyDep, logs: LogsDep, live: ServingDep
) -> None:
    """One entry of a call this gateway opened, with the seq the store stamps on it."""
    if said.type not in EVENTS:
        raise HTTPException(status_code=400, detail=UNKNOWN_EVENT.format(type=said.type))
    refuse_another_orgs_call(live, key, call)
    await _the_open_log(logs, call).append(said.type, dict(said.data), said.ephemeral)


@router.post("/v1/calls/{call}/sealed", status_code=HTTP_204_NO_CONTENT)
async def sealed(call: str, key: AppKeyDep, logs: LogsDep, live: ServingDep) -> None:
    """The call is over: every reader finishes, and nothing more can be appended to it."""
    refuse_another_orgs_call(live, key, call)
    await _the_open_log(logs, call).seal()
    logs.forget(call)
    live.close(call)


# A tenant's worker opens its own org's calls; the fleet's opens every org's, by the call.
def _whose_call(key: KeyRecord, context: CallContext) -> tuple[str, Env, str | None]:
    """The org, the world and the corner a worker's call is opened in, or 403 naming why not."""
    fleet = is_fleet_key(key)
    if not fleet and context.route.org != key.org:
        raise HTTPException(status_code=403, detail=NOT_THIS_ORG)
    if not fleet and context.env != key.env:
        raise HTTPException(
            status_code=403, detail=NOT_THIS_ENV.format(key=key.env, route=context.env)
        )
    return context.route.org, context.env, context.holder if fleet else is_held_by(key)


# The org was said once, at the door that opened the call, and the process kept it: the check
# costs nothing, and a worker of one org cannot write into another's log by knowing a call id.
# Every door a worker names a call at asks this first — append, seal, a tool, the commands — so
# the id alone opens nothing: tools.py and commands.py took the id on faith until 2026-09-26.
def refuse_another_orgs_call(live: Serving, key: KeyRecord, call: str) -> None:
    """403 when this call was opened under some other org than the key's."""
    if is_fleet_key(key):
        return
    org = live.org_of(call)
    if org is not None and org != key.org:
        raise HTTPException(status_code=403, detail=NOT_THIS_ORG)


# Which agent writes a call is said once, at POST /v1/calls, and remembered by the process that
# opened it. A worker that appends to a call this gateway never opened is not writing a log — it
# is writing a log nobody can read back, under an agent nobody named.
def _the_open_log(logs: Logs, call: str) -> CallLog:
    """The log this gateway is writing for that call, or a refusal naming what is missing."""
    log = logs.opened(call)
    if log is None:
        raise HTTPException(status_code=404, detail=NOT_OPEN.format(call=call))
    return log
