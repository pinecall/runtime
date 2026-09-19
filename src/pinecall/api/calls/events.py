"""The doors onto a log: what a reader may ask for, and what the worker writing a call sends."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from starlette.responses import StreamingResponse

from pinecall.api._deps import (
    AdmissionDep,
    AppKeyDep,
    KeysDep,
    LogsDep,
    SettingsDep,
    SnapshotsDep,
    StoreDep,
    TokensDep,
    TuningDep,
)
from pinecall.api._serving import Serving, ServingDep
from pinecall.api.agents.registry import NO_UNCLAIMED, NOT_THAT_APP, RegistryDep
from pinecall.api.agents.tuned import tuned_for
from pinecall.api.calls.opening import how_it_arrived, who_serves
from pinecall.api.calls.sink import (
    AcceptDep,
    CursorDep,
    FilterDep,
    LimitDep,
    ProjectDep,
    ReaderDep,
    ended,
    page,
    reading,
    refuse_another_call,
    refuse_another_org,
    sse,
    wants_sse,
)
from pinecall.api.supervise.aiming import STEERS, QueueingDep, VerbRefused, aimed, as_a_verb
from pinecall.auth.bearer import POLICY_VIOLATION, as_a_close_reason
from pinecall.auth.keys import KeyRecord, held_by, is_the_fleets, not_opening
from pinecall.auth.scopes import Reader
from pinecall.log.entry import Entry, unstored
from pinecall.log.filters import EVERYTHING
from pinecall.log.logs import CallLog
from pinecall.log.store import DEFAULT_LIMIT, Store
from pinecall.log.writers import Logs
from pinecall.orgs.admission import QuotaExhausted
from pinecall.tokens.spending import spent
from pinecall.types import AgentConfig, CallContext
from pinecall_protocol import ProtocolError, WireModel, encode
from pinecall_protocol.events import ErrorEvent
from pinecall_protocol.registry import EVENTS, TERMINAL_EVENT

router = APIRouter()

# Nothing more will ever be true of this call and the reader has all of it. An empty page would be
# a promise to come back, and 200 with `live: false` is what the JSON flavour says; a stream has no
# body to say it in, so the status says it once and the reader stops reconnecting.
NOTHING_MORE = 204

# A frame this socket could not read as one of the six verbs. The pydantic sentence rides with it,
# so a desk with a typo in its JSON learns which field, not just that something was wrong.
BAD_VERB = "bad_verb"

# A verb this socket read and will not apply: the wrong call, a call that ended, nothing running
# here. The sentence is aiming.py's own, so the socket and POST /v1/calls/{call}/verbs refuse in
# the same words.
VERB_REFUSED = "verb_refused"

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


# ── one call's log ──────────────────────────────────────────────────────────────


# response_model=None: one URL, two flavours — FastAPI cannot make a model out of "or a stream".
@router.get("/v1/calls/{call}/events", response_model=None)
async def events(
    call: str,
    reader: ReaderDep,
    store: StoreDep,
    logs: LogsDep,
    project: ProjectDep,
    cursor: CursorDep,
    filter: FilterDep,
    accept: AcceptDep = None,
    limit: LimitDep = DEFAULT_LIMIT,
) -> Response | StreamingResponse | dict[str, object]:
    """The call's entries above the cursor: SSE when the reader asked for it, a page otherwise."""
    refuse_another_call(reader, call)
    await refuse_another_org(reader, store, call, "")
    over = await ended(store, call)
    if over and cursor >= await store.latest_seq(call):
        return Response(status_code=NOTHING_MORE)
    if wants_sse(accept):
        # logs.reading() hands back the live log when this process is writing the call, so the
        # stream goes on into the fanout; for a call nobody here is writing it reads the store and
        # then waits, which is what a reader of another gateway's call should do.
        return sse(logs.reading(call).stream(after=cursor, filter=filter), project, reader)
    entries: list[Entry] = await store.since(call, after=cursor, limit=limit)
    # The filter is applied here, at the sink, and the seq of what survives is untouched: a reader
    # that narrows its view still holds cursors it can hand back to this same endpoint.
    kept = [entry for entry in entries if filter.passes(entry)]
    return page(entries, kept, not over, project, reader)


# ── the agent's own log ─────────────────────────────────────────────────────────


# `live` is always true here and there is no 204: an agent's log ends when the agent stops
# existing, which is not an event. A reader of this URL is meant to hold it open for ever.
# response_model=None: this route answers a page or a stream, and only Accept knows which.
@router.get("/v1/agents/{slug}/calls", response_model=None)
async def calls(
    slug: str,
    reader: ReaderDep,
    store: StoreDep,
    logs: LogsDep,
    project: ProjectDep,
    cursor: CursorDep,
    filter: FilterDep,
    accept: AcceptDep = None,
    limit: LimitDep = DEFAULT_LIMIT,
) -> StreamingResponse | dict[str, object]:
    """What happened to this agent outside any call: registered, configured, an error, a call."""
    refuse_another_call(reader, None)
    await refuse_another_org(reader, store, None, slug)
    if wants_sse(accept):
        # ends_at None: no event terminates this log, so the body closes when the reader leaves.
        stream = logs.reading_agent(slug).stream(after=cursor, filter=filter)
        return sse(stream, project, reader, ends_at=None)
    entries: list[Entry] = await store.agent_since(slug, after=cursor, limit=limit)
    kept = [entry for entry in entries if filter.passes(entry)]
    return page(entries, kept, True, project, reader)


# ── a supervisor's socket ───────────────────────────────────────────────────────


# The socket reads the call downwards and takes the six verbs upwards. Both halves are the
# supervisor's, and the verbs go through the very same aiming.aimed() that POST
# /v1/calls/{call}/verbs calls: one set of checks, whichever door a desk knocked at.
@router.websocket("/v1/attach")
async def attach(
    websocket: WebSocket,
    keys: KeysDep,
    settings: SettingsDep,
    logs: LogsDep,
    store: StoreDep,
    project: ProjectDep,
    registry: RegistryDep,
    snapshots: SnapshotsDep,
    live: QueueingDep,
    call: Annotated[str, Query()],
    token: Annotated[str | None, Query()] = None,
    after: Annotated[int, Query(ge=0)] = 0,
) -> None:
    """One supervisor, one call: the entries down the socket, and the verbs back up it."""
    reader = await reading(websocket, keys, settings, token)
    if reader is None or (reader.call is not None and reader.call != call):
        await websocket.close(code=POLICY_VIOLATION)
        return
    if reader.key is not None and await _another_orgs(reader.key.org, store, call):
        await websocket.close(code=POLICY_VIOLATION)
        return
    await websocket.accept()
    # A key on this socket reads the call and steers it, so it is asked for the second: a person
    # who may only watch is told so in the one sentence, and the socket closes.
    if reader.key is not None and (closed := not_opening(reader.key, STEERS)) is not None:
        await websocket.close(code=POLICY_VIOLATION, reason=as_a_close_reason(closed))
        return
    tail = asyncio.ensure_future(_tail(websocket, logs, project, reader, call, after))
    try:
        # receive(), not receive_json(): a frame that is not JSON at all is answered by name here
        # rather than closing the socket under a desk that mistyped one message.
        while (frame := await websocket.receive())["type"] != "websocket.disconnect":
            said = await _verb(live, registry, snapshots, reader, call, frame.get("text"))
            if said is not None:
                await websocket.send_json(encode(said))
    except WebSocketDisconnect:
        pass
    finally:
        tail.cancel()


# The answer to an inbound frame is either nothing — the verb is on the worker's queue and the
# log is where its effect shows up — or one error entry no log keeps: every log numbers from 1, so
# seq 0 reads as "this was never written down", the same shape the app socket uses.
async def _verb(
    live: QueueingDep,
    registry: RegistryDep,
    snapshots: SnapshotsDep,
    reader: Reader,
    call: str,
    frame: str | None,
) -> Entry | None:
    """One frame as a verb, aimed at the call. None means it went; an entry says why it did not."""
    try:
        said = as_a_verb(frame)
    except ProtocolError as malformed:
        return _an_error(BAD_VERB, str(malformed))
    try:
        await aimed(live, registry, snapshots, reader, call, said)
    except VerbRefused as refused:
        return _an_error(VERB_REFUSED, refused.detail)
    return None


def _an_error(code: str, why: str) -> Entry:
    """One refusal as the entry the socket sends back, with no seq because no log kept it."""
    return unstored("error", ErrorEvent(code=code, message=why, recoverable=True))


async def _tail(
    websocket: WebSocket,
    logs: LogsDep,
    project: ProjectDep,
    reader: Reader,
    call: str,
    after: int,
) -> None:
    """The call's log down the socket, entry by entry, ending where the call does."""
    async for entry in logs.reading(call).stream(after=after, filter=EVERYTHING):
        said = project(entry, reader)
        if said is not None:
            await websocket.send_json(said)
        if entry.type == TERMINAL_EVENT:
            return


# A socket has no 403 to answer with, so the sink's rule is asked as a question here and the
# answer is the same close code every refused socket gets.
async def _another_orgs(org: str, store: Store, call: str) -> bool:
    """Whether this call's log belongs to some other org than the key's."""
    owner = await store.owner(call, "")
    return owner is not None and owner != org


# ── the worker writing a call ───────────────────────────────────────────────────


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
