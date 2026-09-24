"""The worker writing a call: open its log, append to it, seal it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import AdmissionDep, AppKeyDep, LogsDep, TokensDep, TuningDep
from pinecall.api._serving import Serving, ServingDep
from pinecall.api.agents.registry import NO_UNCLAIMED, NOT_THAT_APP, RegistryDep
from pinecall.api.agents.tuned import tuned_for
from pinecall.api.calls.events import NOTHING_MORE
from pinecall.api.calls.opening import how_it_arrived, who_serves
from pinecall.auth.keys import KeyRecord, held_by, is_the_fleets
from pinecall.log.logs import CallLog
from pinecall.log.writers import Logs
from pinecall.orgs.admission import QuotaExhausted
from pinecall.tokens.spending import spent
from pinecall.types import AgentConfig, CallContext
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
@router.post("/v1/calls", status_code=NOTHING_MORE)
async def opened(
    said: Opening,
    key: AppKeyDep,
    logs: LogsDep,
    registry: RegistryDep,
    live: ServingDep,
    tokens: TokensDep,
    admission: AdmissionDep,
    tuning: TuningDep,
) -> None:
    """A call started: open its log, put it on the app's socket, and write how it arrived."""
    context = said.context
    # A tenant's worker opens its own org's calls; the fleet's opens every org's, by the call.
    fleet = is_the_fleets(key)
    if not fleet and context.route.org != key.org:
        raise HTTPException(status_code=403, detail=NOT_THIS_ORG)
    if not fleet and context.env != key.env:
        raise HTTPException(
            status_code=403, detail=NOT_THIS_ENV.format(key=key.env, route=context.env)
        )
    org, env = context.route.org, context.env
    holder = context.holder if fleet else held_by(key)
    # A call a token opened is opened once: the second dispatch with the same token is refused
    # here, before a log exists for it, with the reason in the agent's own log.
    await spent(context, said.agent, tokens, logs)
    # The org's quotas, against the calls open here and what its log says it has consumed. The
    # refusal is in the agent's log before the worker hears the 429, and the sentence is the same.
    try:
        await admission.a_call(org, said.agent, live.running(org))
    except QuotaExhausted as refused:
        raise HTTPException(429, str(refused)) from refused
    # Which process serves this call is asked here exactly as the chat door asks it, of the same
    # function: an app id that names no holder of this agent is refused, never quietly ignored.
    # Whose corner, though, depends on how the call ARRIVED. A number is the org's door and the
    # worker that dialled it holds a key naming nobody, so a ring lands on the LINE — nobody's
    # corner in production, and in the sandbox the developer who claimed it. Everything else was
    # opened BY a key holder, and lands in theirs. See api/agents/doors.py.
    serving = who_serves(registry, env, said.agent, said.app, context, holder)
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
    await how_it_arrived(log, context, said.agent)


@router.post("/v1/calls/{call}/events", status_code=NOTHING_MORE)
async def append(
    call: str, said: Appending, key: AppKeyDep, logs: LogsDep, live: ServingDep
) -> None:
    """One entry of a call this gateway opened, with the seq the store stamps on it."""
    if said.type not in EVENTS:
        raise HTTPException(status_code=400, detail=UNKNOWN_EVENT.format(type=said.type))
    _refuse_another_orgs_call(live, key, call)
    await _the_open_log(logs, call).append(said.type, dict(said.data), said.ephemeral)


@router.post("/v1/calls/{call}/sealed", status_code=NOTHING_MORE)
async def sealed(call: str, key: AppKeyDep, logs: LogsDep, live: ServingDep) -> None:
    """The call is over: every reader finishes, and nothing more can be appended to it."""
    _refuse_another_orgs_call(live, key, call)
    await _the_open_log(logs, call).seal()
    logs.forget(call)
    live.close(call)


# The org was said once, at the door that opened the call, and the process kept it: the check
# costs nothing, and a worker of one org cannot write into another's log by knowing a call id.
def _refuse_another_orgs_call(live: Serving, key: KeyRecord, call: str) -> None:
    """403 when this call was opened under some other org than the key's."""
    if is_the_fleets(key):
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
