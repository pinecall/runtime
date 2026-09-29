"""The call doors a worker writes through and a reader reads: open, append, seal, the log."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from pinecall.domain.agent import AgentConfig, Versions
from pinecall.domain.call import CallContext
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotFound,
)
from pinecall.domain.names import JsonObject
from pinecall.domain.person import THE_FLEET
from pinecall.domain.scope import Scope
from pinecall.gateway import _deps, _streams
from pinecall.gateway._call_setup import tuned
from pinecall.gateway._deps import (
    Acting,
    CallsKey,
    GatewayDep,
    Reader,
    ReaderDep,
    ScopeDep,
    WorkerKey,
    asked_by,
)
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._seal import remembered, sealed
from pinecall.gateway._served import (
    Served,
    attach,
    claim_code,
    looked_up,
    opened,
    served_call,
    serving_agent,
)
from pinecall.gateway._sockets import NO_AGENT, NO_UNCLAIMED, NOT_THAT_APP, Registration
from pinecall.gateway._streams import frame, paced, streamed, wants_sse
from pinecall.log import queries
from pinecall.log.readers import Filter, parse_filter, project_entry, project_state
from pinecall.log.store import DEFAULT_LIMIT, Claim
from pinecall.providers import catalog
from pinecall.providers.catalog import judge_ceiling
from pinecall.session.call import ToolUse
from pinecall.tenancy import disclosure, erasure, keys, orgs, policy, tokens
from pinecall.wire.commands import CallClaim
from pinecall.wire.events import (
    EVENTS,
    TERMINAL_EVENT,
    ErrorEvent,
    ToolCall,
)
from pinecall.wire.frames import Command, Entry
from pinecall.wire.parts import ToolResult
from pinecall.wire.rest.agents import JudgingSettings
from pinecall.wire.rest.calls import (
    AppendEntryRequest,
    CallList,
    CallRow,
    Erasure,
    LogPage,
    LookupRequest,
    LookupResponse,
    OpenCallRequest,
    OpenCallResponse,
    RememberResponse,
    SealCallRequest,
    SessionScore,
)

router = APIRouter()


A_SCREENFUL = 20


UNKNOWN_EVENT = "no event is called {kind!r}: the log takes the protocol's own words"


NOT_OPEN = "this gateway is not writing call {call!r}: open it with POST /v1/calls first"


SEALED = "call {call!r} is over: nothing more can be written to it"


SPENT = "token_spent"


ALREADY_SPENT = "call {call} was opened by its token already: a token opens one call, once"


NEVER_MINTED = "call {call} names a token this runtime never minted"


NOT_RECORDED = "call {call} kept no recording"


NOT_HERE = "the recording of {call} is at {path} on the box that took the call, not on this one"


STILL_LIVE = "call {call} is still running: it can be erased once it has ended"


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


class LogQuery(BaseModel):
    """What a log's reader asks for: the cursor, the types, only what is kept, a page's size."""

    after: int = Field(0, ge=0)
    types: str | None = None
    durable: bool = False
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=DEFAULT_LIMIT)


class StreamOptions(BaseModel):
    """The headers that choose a stream over a page, and where a reconnecting stream resumes."""

    accept: str | None = None
    last_event_id: str | None = None


class ListQuery(BaseModel):
    """What a list of calls asks for: an agent, words, a channel, a page below a call."""

    limit: int = Field(A_SCREENFUL, ge=1, le=_deps.LONGEST_LIST)
    q: str | None = Field(None, max_length=200)
    agent: str | None = None
    channel: str | None = None
    before: str | None = None


# The log opens before the media, so a console sees the call ring; call.started is the worker's.
@router.post("/v1/calls")
async def open_call(body: OpenCallRequest, key: WorkerKey, gateway: GatewayDep) -> OpenCallResponse:
    """Open a call's log, serve it to its agent's socket, say its minutes and its first words."""
    context = body.context
    scope = _call_corner(key, context)
    await _spent(gateway, context, body.agent)
    ceiling = await _deps.admit_call(gateway, scope, body.agent)
    found = serving_agent(gateway.sockets, scope, body.agent, body.app, context)
    _refuse_unserved(gateway, scope, body, found)
    config, versions = await _tuned(gateway, scope, body.agent, found)
    await gateway.logs.store.claim(context.call, body.agent, scope.org, Claim(scope, versions))
    owner = None if found is None else found.owner
    served_call(gateway.serving, owner, context, config, scope)
    served = served_call(gateway.serving, owner, context, config, scope)
    await opened(served.log, context, body.agent)
    first, notice = await _opening(gateway, scope, config.language)
    return OpenCallResponse(
        seconds_left=None if ceiling is None else ceiling.seconds,
        minutes=None if ceiling is None else ceiling.minutes,
        disclosure=first if context.direction == "outbound" else None,
        recording_notice=notice,
    )


# A gateway that restarted forgot the call: served again from the worker's word, no quota, no
# token, no second call.ringing; the socket holding its agent hears call.attached.
@router.post("/v1/calls/{call}/reopened", status_code=204)
async def reopen_call(
    call: str, body: OpenCallRequest, key: WorkerKey, gateway: GatewayDep
) -> None:
    """Serve again a call the gateway forgot."""
    if call != body.context.call:
        raise DeclarationRefused(f"the context is call {body.context.call!r}")
    if gateway.live.calls.get(call) is not None:
        return
    scope = _call_corner(key, body.context)
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None:
        raise NotFound(NOT_OPEN.format(call=call))
    if kept.scope is None or kept.scope.org != scope.org:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    if kept.sealed:
        raise Conflict(SEALED.format(call=call))
    registration = gateway.sockets.serving(scope, body.agent, None)
    config, _ = await _tuned(gateway, scope, body.agent, registration)
    served_call(gateway.serving, None, body.context, config, scope)
    if registration is not None:
        await attach(gateway.live, gateway.logs.store, call, registration.owner)


# The entry comes back with its seq: only the gateway numbers a log.
@router.post("/v1/calls/{call}/events")
async def append_entry(
    call: str, body: AppendEntryRequest, key: WorkerKey, gateway: GatewayDep
) -> Entry:
    """Write one entry of a call this gateway serves."""
    if body.type not in EVENTS:
        raise DeclarationRefused(UNKNOWN_EVENT.format(kind=body.type))
    _orgs_call(gateway, key, call)
    served = _orgs_call(gateway, key, call)
    return await served.log.append(body.type, body.data, ephemeral=body.ephemeral)


@router.post("/v1/calls/{call}/sealed", status_code=204)
async def seal_call(call: str, body: SealCallRequest, key: WorkerKey, gateway: GatewayDep) -> None:
    """Price the call, write its summary and score, and seal its log."""
    await sealed(gateway.serving, _orgs_call(gateway, key, call), body, lent=body.lent)


# The gateway writes tool.call and tool.result: appending the call is what reaches the app.
@router.post("/v1/calls/{call}/tools")
async def run_tool(
    call: str,
    agent: Annotated[str, Query()],
    tool_call: ToolCall,
    key: WorkerKey,
    gateway: GatewayDep,
) -> ToolResult:
    """Run a worker's tool call through the app that holds its agent, and answer its result."""
    _orgs_call(gateway, key, call)
    served = _orgs_call(gateway, key, call)
    if agent != served.agent:
        raise NotFound(NOT_OPEN.format(call=call))
    # Parked because nobody holds the agent: a refusal now, not a wait until the tool's deadline.
    if served.app is None and gateway.sockets.of(served.scope, agent) is None:
        raise Conflict(NO_AGENT.format(slug=agent))
    use = ToolUse(
        call_id=tool_call.call_id, name=tool_call.name, arguments=dict(tool_call.arguments)
    )
    return await served.tools.ran(use, tool_call.speech_id)


# No id: commands have no seq, and a worker that loses the stream asks again.
@router.get("/v1/calls/{call}/commands")
async def stream_commands(call: str, key: WorkerKey, gateway: GatewayDep) -> StreamingResponse:
    """The app's commands for the call, in order, until it is sealed."""
    _orgs_call(gateway, key, call)
    served = _orgs_call(gateway, key, call)
    return streamed(_commanded(served.commands), gateway.closing)


@router.get("/v1/calls/{call}/judging")
async def call_judging(call: str, key: WorkerKey, gateway: GatewayDep) -> JudgingSettings:
    """Whether the call's org judges its calls at hang-up."""
    _orgs_call(gateway, key, call)
    served = _orgs_call(gateway, key, call)
    pool = gateway.connections.pool
    on = await orgs.judged(pool, served.scope.org)
    return JudgingSettings(on=on, ceiling_usd=judge_ceiling(await catalog.providers(pool)))


# The gateway writes memory.ops and docs.sources on the log itself: the worker has no database.
@router.post("/v1/calls/{call}/lookup")
async def lookup(
    call: str, body: LookupRequest, key: WorkerKey, gateway: GatewayDep
) -> LookupResponse:
    """Recall or search for a call served here, answered as the model reads it."""
    served = _orgs_call(gateway, key, call)
    started = time.perf_counter()
    output = await looked_up(gateway.serving, served, body)
    return LookupResponse(output=output, took_ms=(time.perf_counter() - started) * 1000)


@router.post("/v1/calls/{call}/remember")
async def remember(call: str, key: WorkerKey, gateway: GatewayDep) -> RememberResponse:
    """Write what the call taught into its contact's memory now, as the seal would."""
    served = _orgs_call(gateway, key, call)
    started = time.perf_counter()
    written = await remembered(gateway.serving, served)
    ops = 0 if written is None else len(written.op.facts)
    return RememberResponse(ops=ops, took_ms=(time.perf_counter() - started) * 1000)


# A code nobody issued is a 404, and an ordinary one: the caller may be dialling an extension.
@router.post("/v1/calls/{call}/claim", status_code=204)
async def claim_keypad_code(
    call: str, call_claim: CallClaim, key: WorkerKey, gateway: GatewayDep
) -> None:
    """The caller keyed a page's code: tie the call to it."""
    served = _orgs_call(gateway, key, call)
    if not await claim_code(gateway.codes, served, call_claim.code, via="keypad"):
        raise NotFound(_deps.NOBODY_ISSUED.format(code=call_claim.code, agent=served.agent))


@router.get("/v1/calls/{call}/events", response_model=None)
async def stream_events(
    call: str,
    reading: ReaderDep,
    gateway: GatewayDep,
    query: Annotated[LogQuery, Query()],
    options: Annotated[StreamOptions, Header()],
) -> Response | StreamingResponse | LogPage:
    """A call's entries above the cursor: a page, or a stream that ends with the call."""
    config = await _deps.check_readable(gateway, reading, call)
    cursor = max(query.after, _seq_of(options.last_event_id))
    only = parse_filter(query.types, durable=query.durable)
    store = gateway.logs.store
    over = await store.sealed(call)
    if over and cursor >= await store.latest_seq(call):
        return Response(status_code=204)
    if wants_sse(options.accept):
        entries = gateway.logs.reading(call).stream(after=cursor, only=only)
        return streamed(_projected(entries, reading, config, ends=True), gateway.closing)
    read = await store.since(call, after=cursor, limit=query.limit)
    return _page(read, only, reading, config, live=not over)


@router.get("/v1/agents/{slug}/calls", response_model=None)
async def stream_agent_events(
    slug: str,
    reading: ReaderDep,
    gateway: GatewayDep,
    query: Annotated[LogQuery, Query()],
    options: Annotated[StreamOptions, Header()],
) -> StreamingResponse | LogPage:
    """An agent's own log: its registrations, its declarations, its errors. It never ends."""
    if reading.acting is None:
        raise NotAllowed(_deps.NOT_YOURS)
    owner = await gateway.logs.store.owner(slug)
    if owner is not None and owner != reading.acting.org and not _deps.is_the_fleet(reading):
        raise NotFound(NO_AGENT.format(slug=slug))
    config = gateway.sockets.declared(slug)
    only = parse_filter(query.types, durable=query.durable)
    if wants_sse(options.accept):
        entries = gateway.logs.agent(slug).stream(after=query.after, only=only)
        return streamed(_projected(entries, reading, config, ends=False), gateway.closing)
    read = await gateway.logs.store.since(f"@{slug}", after=query.after, limit=query.limit)
    return _page(read, only, reading, config, live=True)


@router.get("/v1/calls/{call}/state")
async def call_state(call: str, reading: ReaderDep, gateway: GatewayDep) -> JsonObject:
    """The call's folded state as this reader may see it, and the seq a stream resumes from."""
    config = await _deps.check_readable(gateway, reading, call)
    state = await gateway.logs.reading(call).snapshot()
    if state.seq == 0:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    viewer = None if reading.visit is None else reading.visit.identity
    return {
        "state": project_state(state, reading.projection, config, viewer),
        "last_seq": state.seq,
        "live": not await gateway.logs.store.sealed(call),
    }


@router.get("/v1/calls/{call}/recording")
async def recording(call: str, reading: ReaderDep, gateway: GatewayDep) -> FileResponse:
    """The call's audio, with byte ranges so a player can seek."""
    await _deps.check_readable(gateway, reading, call)
    state = await gateway.logs.reading(call).snapshot()
    summary = next(
        (
            item
            for item in reversed(await gateway.logs.store.whole(call))
            if item.type == "call.summary"
        ),
        None,
    )
    pointer = None if summary is None else summary.data.get("recording")
    if state.seq == 0 or not isinstance(pointer, str) or not pointer:
        raise NotFound(NOT_RECORDED.format(call=call))
    path = Path(pointer)
    if not await asyncio.to_thread(path.is_file):
        raise NotFound(NOT_HERE.format(call=call, path=pointer))
    return FileResponse(path, media_type="audio/ogg", filename=f"{call}.ogg")


# The one door that deletes from a call's log: through erasure's own path, never the trigger's.
@router.delete("/v1/calls/{call}")
async def erase_call(call: str, key: CallsKey, where: ScopeDep, gateway: GatewayDep) -> Erasure:
    """Erase one call: its log, facts, tokens, the memories it taught and its recording."""
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None or kept.scope is None or not _sees_to_erase(where, kept.scope):
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    if not kept.sealed:
        raise Conflict(STILL_LIVE.format(call=call))
    recordings = Path(gateway.connections.settings.recordings_root)
    erased = await erasure.call(
        gateway.connections.pool, recordings, kept.scope, call, by=asked_by(key)
    )
    gateway.logs.forget(call)
    return erased.trail


@router.get("/v1/agents/{slug}/sessions")
async def list_agent_calls(
    slug: str, reading: ReaderDep, gateway: GatewayDep, query: Annotated[ListQuery, Query()]
) -> CallList:
    """The agent's newest calls, one row each."""
    wanted = queries.ListFilters(
        agent=slug, q=query.q or None, channel=query.channel, before=query.before
    )
    return await _sessions(gateway, reading, wanted, query.limit)


@router.get("/v1/sessions")
async def list_calls(
    reading: ReaderDep, gateway: GatewayDep, query: Annotated[ListQuery, Query()]
) -> CallList:
    """The org's newest calls across its agents, one row each."""
    wanted = queries.ListFilters(
        agent=query.agent or None, q=query.q or None, channel=query.channel, before=query.before
    )
    return await _sessions(gateway, reading, wanted, query.limit)


# Live only, no cursor: every entry of the feed is also in a log, which is where to resume.
@router.get("/v1/events", response_model=None)
async def stream_org_events(reading: ReaderDep, gateway: GatewayDep) -> StreamingResponse:
    """The org's calls and agents changing, as they change."""
    if reading.acting is None:
        raise NotAllowed(_deps.NOT_YOURS)
    feed = gateway.logs.feed(reading.acting.org, reading.acting.env).subscribe()
    return streamed(_projected(feed, reading, None, ends=False), gateway.closing)


# A tenant's worker opens its own org's calls in its own world; the fleet's opens any org's in
# its world, in the scope the dispatch named.
def _call_corner(key: Acting, context: CallContext) -> Scope:
    named = Scope(context.route.org, context.env, context.holder or "")
    fleet = THE_FLEET in key.bearer.key.scopes
    if not fleet and context.route.org != key.org:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=context.call))
    if not fleet and context.env != key.env:
        raise NotAllowed(
            f"this key opens {key.env}, and that call's route answers in {context.env}"
        )
    return keys.scope_of(key.bearer, key.env, dispatched=named)


# An id grants nothing: another org's or world's call is the same 404 as a call nobody opened.
# The fleet's key serves every org of its world; a tenant's worker its own org alone.
def _orgs_call(gateway: Gateway, key: Acting, call: str) -> Served:
    served = gateway.live.calls.get(call)
    fleet = THE_FLEET in key.bearer.key.scopes
    if served is None or served.scope.env != key.env:
        raise NotFound(NOT_OPEN.format(call=call))
    if not fleet and served.scope.org != key.org:
        raise NotFound(NOT_OPEN.format(call=call))
    return served


async def _spent(gateway: Gateway, context: CallContext, agent: str) -> None:
    scope = context.metadata.get("scope")
    if not isinstance(scope, str):
        return
    spending = await tokens.spend(gateway.connections.pool, context.call)
    if spending == "spent":
        return
    sentence = (ALREADY_SPENT if spending == "already_spent" else NEVER_MINTED).format(
        call=context.call
    )
    refused = ErrorEvent(code=SPENT, message=sentence, recoverable=True)
    await gateway.logs.agent(agent).append("error", refused.written())
    raise Conflict(sentence)


def _refuse_unserved(
    gateway: Gateway, scope: Scope, body: OpenCallRequest, registration: Registration | None
) -> None:
    if body.app is not None and registration is None:
        raise Conflict(NOT_THAT_APP.format(app=body.app, slug=body.agent))
    # Held by consoles alone: refused before the greeting, rather than a call with no app.
    if registration is None and gateway.sockets.of(scope, body.agent) is not None:
        raise Conflict(NO_UNCLAIMED.format(slug=body.agent))


async def _tuned(
    gateway: Gateway, scope: Scope, agent: str, registration: Registration | None
) -> tuple[AgentConfig, Versions]:
    declared = gateway.sockets.of(scope, agent) if registration is None else registration
    config = AgentConfig(slug=agent) if declared is None else declared.config
    return await tuned(
        gateway.connections.pool, config, scope, await catalog.providers(gateway.connections.pool)
    )


# The worker says the disclosure before the greeting, and the notice only where it records.
async def _opening(
    gateway: Gateway, scope: Scope, language: str | None
) -> tuple[str | None, str | None]:
    pool = gateway.connections.pool
    kept = (await policy.policy_of(pool, scope.org)).policy
    org = await orgs.find(pool, scope.org)
    name = scope.org if org is None else org.name
    return disclosure.disclosure_of(kept, name, language), disclosure.notice_of(kept, language)


def _page(
    read: list[Entry], only: Filter, reading: Reader, config: AgentConfig | None, *, live: bool
) -> LogPage:
    viewer = None if reading.visit is None else reading.visit.identity
    kept = (
        project_entry(item, reading.projection, config, viewer)
        for item in read
        if only.passes(item)
    )
    # `next` is the last seq read, not kept, so a page that kept nothing still moves the cursor.
    return LogPage(
        entries=[kept_one for kept_one in kept if kept_one is not None],
        live=live,
        next=read[-1].seq if read else None,
    )


async def _projected(
    entries: AsyncIterator[Entry], reading: Reader, config: AgentConfig | None, *, ends: bool
) -> AsyncIterator[str]:
    viewer = None if reading.visit is None else reading.visit.identity
    yield f"retry: {_streams.RETRY_MS}\n\n"
    async for entry in paced(entries):
        if entry is None:
            yield _streams.PING
            continue
        data = project_entry(entry, reading.projection, config, viewer)
        if data is not None:
            yield frame(entry.type, data, seq=entry.seq)
        if ends and entry.type == TERMINAL_EVENT:
            return


async def _commanded(waiting: asyncio.Queue[Command | None]) -> AsyncIterator[str]:
    async def taken() -> AsyncIterator[Command]:
        while (command := await waiting.get()) is not None:
            yield command

    async for command in paced(taken()):
        yield _streams.PING if command is None else frame(command.type, command.written())


async def _sessions(
    gateway: Gateway, reading: Reader, wanted: queries.ListFilters, limit: int
) -> CallList:
    if reading.acting is None or reading.scope is None:
        raise NotAllowed("a list of calls is read with a key, not a call's token")
    found = await queries.found(gateway.connections.pool, reading.scope, wanted, limit=limit)
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


def _seq_of(header: str | None) -> int:
    try:
        return max(int(header or 0), 0)
    except ValueError:
        return 0


# A key erases what it may read: its org and world, and a colleague's corner only as the org's own.
def _sees_to_erase(where: Scope, owner: Scope) -> bool:
    same = where.org == owner.org and where.env == owner.env
    return same and owner.holder in ("", where.holder)
