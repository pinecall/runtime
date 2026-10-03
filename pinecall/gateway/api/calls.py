"""The call doors a worker writes through and a reader reads: open, append, seal, the log."""

import asyncio
import base64
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pinecall.channels import routes
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
    CallReaderDep,
    GatewayDep,
    Reader,
    ReaderDep,
    ScopeDep,
    TeamKey,
    WorkerKey,
    asked_by,
)
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import (
    Served,
    claim_code,
    first_seen,
    looked_up,
    opened,
    served_call,
    serving_agent,
)
from pinecall.gateway._sockets import NO_AGENT, NO_UNCLAIMED, NOT_THAT_APP, Registration
from pinecall.gateway._streams import frame, paced, streamed, wants_sse
from pinecall.gateway.calls.binding import attach
from pinecall.gateway.ending.seal import remembered, sealed
from pinecall.log import openings, queries
from pinecall.log.readers import Filter, parse_filter, project_entry, project_state
from pinecall.log.store import DEFAULT_LIMIT, Claim
from pinecall.process.recordings import (
    recordings_of,
)
from pinecall.providers import catalog
from pinecall.providers.build import vendor_named_in
from pinecall.providers.catalog import judge_ceiling
from pinecall.session.call import ToolUse
from pinecall.tenancy import (
    disclosure,
    erasure,
    keys,
    orgs,
    policy,
    recording_keys,
    tokens,
)
from pinecall.tenancy.scopes import Picked
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
    AppendEntriesRequest,
    AppendEntriesResponse,
    AppendEntryRequest,
    CallList,
    CallRow,
    Erasure,
    LogPage,
    LookupRequest,
    LookupResponse,
    OpenCallRequest,
    OpenCallResponse,
    RecordingKeyResponse,
    RememberResponse,
    SealCallRequest,
    SessionScore,
)

router = APIRouter()


logger = logging.getLogger(__name__)


A_SCREENFUL = 20


UNKNOWN_EVENT = "no event is called {kind!r}: the log takes the protocol's own words"


NOT_OPEN = "this gateway is not writing call {call!r}: open it with POST /v1/calls first"


SEALED = "call {call!r} is over: nothing more can be written to it"


SPENT = "token_spent"


ERROR = "error"


SWITCHED = "vendor.switched"


ALREADY_SPENT = "call {call} was opened by its token already: a token opens one call, once"


ELSEWHERE = "call %s was opened in org %s, %s, agent %s: its token was minted for another"


UNOPENED_KEY = "the key of call {call}'s recording is sealed under a key the vault no longer lists"


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
    keys.check_agent(key.bearer, body.agent)
    scope = _call_corner(key, context)
    # The carrier, the fence and the world rule worked, whatever is refused below.
    if context.direction == "inbound" and context.route.number is not None:
        await routes.called(gateway.connections.pool, scope.org, context.route.number)
    await _unclaimed_or_in(gateway, context.call, scope)
    await _spent(gateway, context, scope, body.agent)
    ceiling = await _deps.admit_call(gateway, scope, body.agent)
    found = serving_agent(gateway.sockets, scope, body.agent, body.app, context)
    _refuse_unserved(gateway, scope, body, found)
    config, versions = await _tuned(gateway, scope, body.agent, found, context.call)
    # Asked again by a worker whose gateway died with the answer: the same call, one ringing.
    again = await openings.opening_of(gateway.connections.pool, context.call) is not None
    await gateway.logs.store.claim(context.call, body.agent, scope.org, Claim(scope, versions))
    await openings.kept(gateway.connections.pool, scope.org, context, config)
    await gateway.prompts.keep(gateway.connections.pool, scope.org, config.knowledge or "")
    owner = None if found is None else found.owner
    served = served_call(gateway.serving, owner, context, config, scope)
    await gateway.live.commands_heard(context.call)
    if not again:
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
    if kept.scope != scope:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    if kept.sealed:
        raise Conflict(SEALED.format(call=call))
    registration = gateway.sockets.serving(scope, body.agent, None)
    config, _ = await _tuned(gateway, scope, body.agent, registration, call)
    await openings.kept(gateway.connections.pool, scope.org, body.context, config)
    served_call(gateway.serving, None, body.context, config, scope)
    if registration is not None:
        await attach(gateway.live, call, registration.owner)


# The entry comes back with its seq: only the gateway numbers a log.
@router.post("/v1/calls/{call}/events")
async def append_entry(
    call: str, body: AppendEntryRequest, key: WorkerKey, gateway: GatewayDep
) -> Entry:
    """Write one entry of a call this gateway serves."""
    if body.type not in EVENTS:
        raise DeclarationRefused(UNKNOWN_EVENT.format(kind=body.type))
    served = await known(gateway, key, call)
    began = time.perf_counter()
    entry = await served.log.append(body.type, body.data, ephemeral=body.ephemeral)
    counted(gateway, time.perf_counter() - began, [entry])
    return entry


# Taken whole or refused whole; the answer to a retry of the last batch is the seqs it was given.
@router.post("/v1/calls/{call}/entries")
async def append_entries(
    call: str, body: AppendEntriesRequest, key: WorkerKey, gateway: GatewayDep
) -> AppendEntriesResponse:
    """Write a worker's batch of a call this gateway serves, once and in order."""
    for item in body.entries:
        if item.type not in EVENTS:
            raise DeclarationRefused(UNKNOWN_EVENT.format(kind=item.type))
    served = await known(gateway, key, call)
    began = time.perf_counter()
    entries = await served.log.append_many(body.entries, after=body.after)
    counted(gateway, time.perf_counter() - began, entries)
    return AppendEntriesResponse(entries=entries)


@router.post("/v1/calls/{call}/sealed", status_code=204)
async def seal_call(call: str, body: SealCallRequest, key: WorkerKey, gateway: GatewayDep) -> None:
    """Price the call, write its summary and score, and seal its log."""
    await sealed(gateway.serving, await known(gateway, key, call), body, lent=body.lent)


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
    served = await known(gateway, key, call)
    if agent != served.agent:
        raise NotFound(NOT_OPEN.format(call=call))
    # Parked because nobody holds the agent: a refusal now, not a wait until the tool's deadline.
    if served.app is None and gateway.sockets.of(served.scope, agent) is None:
        raise Conflict(NO_AGENT.format(slug=agent))
    use = ToolUse(
        call_id=tool_call.call_id, name=tool_call.name, arguments=dict(tool_call.arguments)
    )
    # Its socket is on another gateway, which may never have heard the call was bound to it.
    if served.app is not None and served.app not in gateway.live.sockets:
        gateway.live.pumped(call, after=0)
    return await served.tools.ran(use, tool_call.speech_id)


# The worker seals the call's recording with it before storing it; asked again, the same key.
@router.post("/v1/calls/{call}/recording/key")
async def recording_key(call: str, key: WorkerKey, gateway: GatewayDep) -> RecordingKeyResponse:
    """The key the call's recording is sealed under, made once for the call."""
    served = await known(gateway, key, call)
    connections = gateway.connections
    found = await recording_keys.key_for(
        connections.pool, connections.vault, served.scope.org, call
    )
    if found is None:
        raise Conflict(UNOPENED_KEY.format(call=call))
    return RecordingKeyResponse(key=base64.urlsafe_b64encode(found).decode())


# No id: commands have no seq, and a worker that loses the stream asks again.
@router.get("/v1/calls/{call}/commands")
async def stream_commands(call: str, key: WorkerKey, gateway: GatewayDep) -> StreamingResponse:
    """The app's commands for the call, in order, until it is sealed."""
    served = await known(gateway, key, call)
    await gateway.live.commands_heard(call)
    return streamed(_commanded(served.commands), gateway.closing)


@router.get("/v1/calls/{call}/judging")
async def call_judging(call: str, key: WorkerKey, gateway: GatewayDep) -> JudgingSettings:
    """Whether the call's org judges its calls at hang-up."""
    served = await known(gateway, key, call)
    pool = gateway.connections.pool
    on = await orgs.judged(pool, served.scope.org)
    return JudgingSettings(on=on, ceiling_usd=judge_ceiling(await catalog.providers(pool)))


# The gateway writes memory.ops and docs.sources on the log itself: the worker has no database.
@router.post("/v1/calls/{call}/lookup")
async def lookup(
    call: str, body: LookupRequest, key: WorkerKey, gateway: GatewayDep
) -> LookupResponse:
    """Recall or search for a call served here, answered as the model reads it."""
    served = await known(gateway, key, call)
    started = time.perf_counter()
    output = await looked_up(gateway.serving, served, body)
    return LookupResponse(output=output, took_ms=(time.perf_counter() - started) * 1000)


@router.post("/v1/calls/{call}/remember")
async def remember(call: str, key: WorkerKey, gateway: GatewayDep) -> RememberResponse:
    """Write what the call taught into its contact's memory now, as the seal would."""
    served = await known(gateway, key, call)
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
    served = await known(gateway, key, call)
    if not await claim_code(gateway.codes, served, call_claim.code, via="keypad"):
        raise NotFound(_deps.NOBODY_ISSUED.format(code=call_claim.code, agent=served.agent))


@router.get("/v1/calls/{call}/events", response_model=None)
async def stream_events(
    call: str,
    reading: CallReaderDep,
    gateway: GatewayDep,
    query: Annotated[LogQuery, Query()],
    options: Annotated[StreamOptions, Header()],
) -> Response | StreamingResponse | LogPage:
    """A call's entries above the cursor: a page, or a stream that ends with the call."""
    config = await _deps.check_readable(gateway, reading, call)
    await _deps.record_read(gateway, reading, call, "log")
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
    if owner is not None and owner != reading.acting.org:
        raise NotFound(NO_AGENT.format(slug=slug))
    config = gateway.sockets.declared(slug)
    only = parse_filter(query.types, durable=query.durable)
    if wants_sse(options.accept):
        entries = gateway.logs.agent(slug).stream(after=query.after, only=only)
        return streamed(_projected(entries, reading, config, ends=False), gateway.closing)
    read = await gateway.logs.store.since(f"@{slug}", after=query.after, limit=query.limit)
    return _page(read, only, reading, config, live=True)


@router.get("/v1/calls/{call}/state")
async def call_state(call: str, reading: CallReaderDep, gateway: GatewayDep) -> JsonObject:
    """The call's folded state as this reader may see it, and the seq a stream resumes from."""
    config = await _deps.check_readable(gateway, reading, call)
    await _deps.record_read(gateway, reading, call, "log")
    state = await gateway.logs.reading(call).snapshot()
    if state.seq == 0:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    viewer = None if reading.visit is None else reading.visit.identity
    return {
        "state": project_state(state, reading.projection, config, viewer),
        "last_seq": state.seq,
        "live": not await gateway.logs.store.sealed(call),
    }


# The one door that deletes from a call's log: through erasure's own path, never the trigger's.
@router.delete("/v1/calls/{call}")
async def erase_call(call: str, key: TeamKey, where: ScopeDep, gateway: GatewayDep) -> Erasure:
    """Erase one call: its log, facts, tokens, the memories it taught and its recording."""
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None or kept.scope is None or not _sees_to_erase(where, kept.scope):
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    keys.check_agent(key.bearer, kept.agent)
    if not kept.sealed:
        raise Conflict(STILL_LIVE.format(call=call))
    recordings = recordings_of(gateway.connections.settings, gateway.connections.http)
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
    feed = await gateway.logs.feed_reader(reading.acting.org, reading.acting.env)
    return streamed(_projected(feed, reading, None, ends=False), gateway.closing)


# An id grants nothing: another org's or world's call is the same 404 as a call nobody opened.
# The fleet's key serves every org of its world; a tenant's worker its own org alone.
# A call this gateway serves, or one another gateway opened, served here from what was kept when
# it opened: one read, the first time a door here asks. A call opened by a release that kept
# nothing is the 404 a worker answers by saying the call again (`/reopened`).
# What a door of a call this gateway serves starts from; the socket door starts from it too.
async def known(gateway: Gateway, key: Acting, call: str) -> Served:
    """The call as this gateway serves it, first seen here if need be; a refusal names why."""
    now = time.monotonic()
    served = gateway.live.calls.get(call)
    if served is not None:
        _yours(key, served.scope, call)
        gateway.live.in_use(call, now)
        return served
    pool = gateway.connections.pool
    kept = await queries.scope_of_call(pool, call)
    if kept is None or kept.scope is None:
        raise NotFound(NOT_OPEN.format(call=call))
    _yours(key, kept.scope, call)
    if kept.sealed:
        raise Conflict(SEALED.format(call=call))
    opening = await openings.opening_of(pool, call)
    if opening is None:
        raise NotFound(NOT_OPEN.format(call=call))
    return first_seen(gateway.serving, opening.context, opening.config, kept.scope, now)


# What /metrics reads: the append's time at the door, and each vendor's failures as they come in.
# A vendor fails a call when an error names its plugin, or when a stage switches away from it.
def counted(gateway: Gateway, seconds: float, entries: list[Entry]) -> None:
    """Count an append's time and entries, and what they say of vendors, for /metrics."""
    counters = gateway.counters
    counters.appended_in(seconds, len(entries))
    at = time.monotonic()
    for entry in entries:
        vendor = ""
        if entry.type == ERROR:
            code, message = entry.data.get("code"), entry.data.get("message")
            vendor = vendor_named_in(message if isinstance(message, str) else "")
            counters.failed(code if isinstance(code, str) else "", vendor)
        elif entry.type == SWITCHED and entry.data.get("available") is False:
            named = entry.data.get("vendor")
            vendor = named if isinstance(named, str) else ""
        if vendor and entry.call is not None:
            counters.failed_on(vendor, entry.call, at)


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


def _yours(key: Acting, scope: Scope, call: str) -> None:
    fleet = THE_FLEET in key.bearer.key.scopes
    if scope.env != key.env or (not fleet and scope.org != key.org):
        raise NotFound(NOT_OPEN.format(call=call))


# A call a dial placed already has its head: the worker opens it in the scope the head keeps, or
# not at all, whatever its key; the first claim of a head stands, so this is the one check.
async def _unclaimed_or_in(gateway: Gateway, call: str, scope: Scope) -> None:
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is not None and kept.scope is not None and kept.scope != scope:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))


# A web call opens by the token its room was minted with: once, and only in the org, the world
# and for the agent it was minted for. Asked of the ledger by the call's id, whatever the worker
# says: a call no token was minted for (a phone, a dial, a run) is not a token's to refuse.
async def _spent(gateway: Gateway, context: CallContext, scope: Scope, agent: str) -> None:
    spending = await tokens.spend(gateway.connections.pool, context.call, scope, agent)
    if spending in {"spent", "never_minted"}:
        return
    if spending == "minted_elsewhere":
        logger.warning(ELSEWHERE, context.call, scope.org, scope.env, agent)
        raise NotFound(_deps.NO_SUCH_CALL.format(call=context.call))
    sentence = ALREADY_SPENT.format(call=context.call)
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


# The call's id picks its version where the scope stands on a canary, the same on a reopen.
async def _tuned(
    gateway: Gateway, scope: Scope, agent: str, registration: Registration | None, call: str
) -> tuple[AgentConfig, Versions]:
    declared = gateway.sockets.of(scope, agent) if registration is None else registration
    config = AgentConfig(slug=agent) if declared is None else declared.config
    configured = await catalog.providers(gateway.connections.pool)
    return await tuned(gateway.connections.pool, config, scope, configured, Picked(call=call))


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
