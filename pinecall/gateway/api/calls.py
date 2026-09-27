"""A call's doors: the worker's writes, the readers, the desk, a visitor's token and a text call."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState

from pinecall.channels import routes
from pinecall.channels.routes import Dispatch
from pinecall.channels.whatsapp import WINDOW_S
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    PinecallError,
    QuotaExhausted,
)
from pinecall.domain.types import (
    THE_FLEET,
    THE_WIDGET,
    AgentConfig,
    CallContext,
    Contact,
    Corner,
    Json,
    JsonObject,
    Route,
    Versions,
    new_call_id,
    parse_channel,
    today_in,
)
from pinecall.gateway import deps
from pinecall.gateway.deps import (
    Acting,
    CallsKey,
    CornerDep,
    Reader,
    ReaderDep,
    TalkKey,
    Wired,
    WiredDep,
    WorkerKey,
    frame,
    paced,
    streamed,
    wants_sse,
)
from pinecall.gateway.live import (
    NO_AGENT,
    NO_LOOKUPS,
    NO_UNCLAIMED,
    NOT_THAT_APP,
    Registration,
    Served,
    attach,
    claim_code,
    exhausted,
    open_text,
    opened,
    resume_text,
    sealed,
    served_call,
    serving_agent,
    tokens_of,
    tuned,
)
from pinecall.log import index
from pinecall.log.log import Subscription
from pinecall.log.readers import Filter, parse_filter, project_entry, project_state
from pinecall.log.store import DEFAULT_LIMIT, Claim
from pinecall.providers import catalog
from pinecall.session import text
from pinecall.session.call import ToolUse
from pinecall.session.session import Session
from pinecall.tenancy import admission, agents, keys, orgs
from pinecall.tenancy.keys import LONGEST_VISIT_TTL_S, MINTED_FOR_A_VISIT, ONE_VISIT_TTL_S
from pinecall.wire.commands import CallClaim, SayVerb, SupervisorVerb, Verb
from pinecall.wire.events import (
    EVENTS,
    TERMINAL_EVENT,
    ErrorEvent,
    FleetFull,
    ToolCall,
)
from pinecall.wire.frames import Command, Entry
from pinecall.wire.parts import Supervisor, ToolResult
from pinecall.wire.rest import (
    Appending,
    Code,
    CodeStanding,
    CodeWanted,
    Judging,
    LogPage,
    Opened,
    Opening,
    Sealing,
    SeatTaken,
    SessionLine,
    SessionList,
    SessionScore,
    Thread,
    ThreadKind,
    ThreadLast,
    ThreadLine,
    ThreadList,
    ThreadMessage,
    ThreadSaid,
    ThreadSay,
    TokenMinted,
    TokenWanted,
    VerbTaken,
)

logger = logging.getLogger(__name__)

A_SCREENFUL = 20
LONGEST_LIST = 200

router = APIRouter()


class LogAsked(BaseModel):
    """What a log's reader asks for: the cursor, the types, only what is kept, a page's size."""

    after: int = Field(0, ge=0)
    types: str | None = None
    durable: bool = False
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=DEFAULT_LIMIT)


class StreamTold(BaseModel):
    """The headers that choose a stream over a page, and where a reconnecting stream resumes."""

    accept: str | None = None
    last_event_id: str | None = None


class ListAsked(BaseModel):
    """What a list of calls asks for: an agent, words, a channel, a page below a call."""

    limit: int = Field(A_SCREENFUL, ge=1, le=LONGEST_LIST)
    q: str | None = Field(None, max_length=200)
    agent: str | None = None
    channel: str | None = None
    before: str | None = None


class CodeAsked(BaseModel):
    """Whether a page's ask waits for its code to be keyed."""

    wait: bool = False


UNKNOWN_EVENT = "no event is called {kind!r}: the log takes the protocol's own words"
NOT_OPEN = "this gateway is not writing call {call!r}: open it with POST /v1/calls first"
SEALED = "call {call!r} is over: nothing more can be written to it"
NO_SUCH_CALL = "no call {call} in this key's org and world"
NOT_YOURS = "this token reads another call"
IS_OVER = "call {call} is over: read its log or its recording instead"
SPENT = "token_spent"
ALREADY_SPENT = "call {call} was opened by its token already: a token opens one call, once"
NEVER_MINTED = "call {call} names a token this runtime never minted"
NOT_A_VISIT = "scope is one of {scopes}: observe and supervise take the API key instead"
NO_AGENT_NAMED = "name the agent: `agent` in the body, or agentName as a livekit client sends it"
OURS_TO_SET = {
    "room_name": "the room is the call id this door mints",
    "participant_name": "a name is PII, and the log would carry it",
    "participant_metadata": "it carries the contact id: send `contact`",
}
NOT_YOURS_TO_SET = "{field} is minted here and refused in the body: {why}"
NO_MEMORY = "this runtime keeps no memory to write the call into"
OUR_ATTRIBUTES = "pinecall."
FLEET_FULL = (
    "every seat of the fleet is taken: {active} calls on {workers} workers. Offer a call back, or "
    "try again in a minute"
)
NO_PHONE = "agent {slug} answers at no phone number in {env}: import one first"
NOBODY_ISSUED = (
    "no code {code} is waiting for agent {agent}: nobody issued it, it expired, or a call took it"
)
NOT_YOUR_CODE = "this token reads another code"
NOT_RECORDED = "call {call} kept no recording"
NOT_HERE = "the recording of {call} is at {path} on the box that took the call, not on this one"
NOT_TAKEN_UP = "call {call} cannot be taken up: it is over, or not this agent's; open a new one"
READS_ONLY = "this token reads the call and sends no verb: steering it takes a supervise token"
A_KEY = "key:{org}"
# 25 s: under the 30 s a proxy lets a request idle.
LONGEST_WAIT_S = 25.0
A_PAGE_MAY_ASK_AFTER_S = 60.0
# What uvicorn closes with on a stop; a caller never sends it, so the call is left for the next
# process to take up.
SERVICE_RESTART = 1012
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


# ── the worker's writes ──


# The log opens before the media, so a console sees the call ring; call.started is the worker's.
@router.post("/v1/calls")
async def open_call(said: Opening, key: WorkerKey, box: WiredDep) -> Opened:
    """Open a call's log, serve it to the socket that holds its agent, and say its minutes."""
    context = said.context
    corner = _call_corner(key, context)
    await _spent(box, context, said.agent)
    ceiling = await _admitted(box, corner, said.agent)
    held = serving_agent(box.registry, corner, said.agent, said.app, context)
    _refuse_unserved(box, corner, said, held)
    config, versions = await _tuned(box, corner, said.agent, held)
    await box.logs.store.claim(context.call, said.agent, corner.org, Claim(corner, versions))
    owner = None if held is None else held.owner
    served = served_call(box.gated, owner, context, config, corner)
    await opened(served.log, context, said.agent)
    if ceiling is None:
        return Opened(seconds_left=None, minutes=None)
    return Opened(seconds_left=ceiling.seconds, minutes=ceiling.minutes)


# A gateway that restarted forgot the call: served again from the worker's word, no quota, no
# token, no second call.ringing; the socket holding its agent hears call.attached.
@router.post("/v1/calls/{call}/reopened", status_code=204)
async def reopen_call(call: str, said: Opening, key: WorkerKey, box: WiredDep) -> None:
    """Serve again a call the gateway forgot."""
    if call != said.context.call:
        raise DeclarationRefused(f"the context is call {said.context.call!r}")
    if box.live.calls.get(call) is not None:
        return
    corner = _call_corner(key, said.context)
    kept = await index.corner_of_call(box.pool, call)
    if kept is None:
        raise NotFound(NOT_OPEN.format(call=call))
    if kept.corner is None or kept.corner.org != corner.org:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    if kept.sealed:
        raise Conflict(SEALED.format(call=call))
    held = box.registry.serving(corner, said.agent, None)
    config, _ = await _tuned(box, corner, said.agent, held)
    served_call(box.gated, None, said.context, config, corner)
    if held is not None:
        await attach(box.live, box.logs.store, call, held.owner)


# The entry comes back with its seq: only the gateway numbers a log.
@router.post("/v1/calls/{call}/events")
async def append(call: str, said: Appending, key: WorkerKey, box: WiredDep) -> Entry:
    """Write one entry of a call this gateway serves."""
    if said.type not in EVENTS:
        raise DeclarationRefused(UNKNOWN_EVENT.format(kind=said.type))
    served = _orgs_call(box, key, call)
    return await served.log.append(said.type, said.data, ephemeral=said.ephemeral)


@router.post("/v1/calls/{call}/sealed", status_code=204)
async def seal_call(call: str, said: Sealing, key: WorkerKey, box: WiredDep) -> None:
    """Price the call, write its summary and score, and seal its log."""
    await sealed(box.gated, _orgs_call(box, key, call), said, lent=said.lent)


# The gateway writes tool.call and tool.result: appending the call is what reaches the app.
@router.post("/v1/calls/{call}/tools")
async def run_a_tool(
    call: str, agent: Annotated[str, Query()], said: ToolCall, key: WorkerKey, box: WiredDep
) -> ToolResult:
    """Run a worker's tool call through the app that holds its agent, and answer its result."""
    served = _orgs_call(box, key, call)
    if agent != served.agent:
        raise NotFound(NOT_OPEN.format(call=call))
    # Parked because nobody holds the agent: a refusal now, not a wait until the tool's deadline.
    if served.app is None and box.registry.of(served.corner, agent) is None:
        raise Conflict(NO_AGENT.format(slug=agent))
    use = ToolUse(call_id=said.call_id, name=said.name, arguments=dict(said.arguments))
    return await served.tools.ran(use, said.speech_id)


# No id: commands have no seq, and a worker that loses the stream asks again.
@router.get("/v1/calls/{call}/commands")
async def commands(call: str, key: WorkerKey, box: WiredDep) -> StreamingResponse:
    """The app's commands for the call, in order, until it is sealed."""
    served = _orgs_call(box, key, call)
    return streamed(_commanded(served.commands), box.closing)


@router.get("/v1/calls/{call}/judging")
async def judging(call: str, key: WorkerKey, box: WiredDep) -> Judging:
    """Whether the call's org judges its calls at hang-up."""
    served = _orgs_call(box, key, call)
    on = await orgs.judged(box.pool, served.corner.org)
    return Judging(on=on, ceiling_eur=box.settings.judge_ceiling_eur)


@router.post("/v1/calls/{call}/lookup")
async def lookup(call: str, key: WorkerKey, box: WiredDep) -> JsonObject:
    """Recall or search for a call; this runtime keeps neither yet."""
    _orgs_call(box, key, call)
    raise NotAvailable(NO_LOOKUPS)


@router.post("/v1/calls/{call}/remember")
async def remember(call: str, key: WorkerKey, box: WiredDep) -> JsonObject:
    """Write a call into its contact's memory; this runtime keeps none yet."""
    _orgs_call(box, key, call)
    raise NotAvailable(NO_MEMORY)


# A code nobody issued is a 404, and an ordinary one: the caller may be dialling an extension.
@router.post("/v1/calls/{call}/claim", status_code=204)
async def keyed(call: str, said: CallClaim, key: WorkerKey, box: WiredDep) -> None:
    """The caller keyed a page's code: tie the call to it."""
    served = _orgs_call(box, key, call)
    if not await claim_code(box.codes, served, said.code, via="keypad"):
        raise NotFound(NOBODY_ISSUED.format(code=said.code, agent=served.agent))


# ── the readers ──


@router.get("/v1/calls/{call}/events", response_model=None)
async def events(
    call: str,
    reading: ReaderDep,
    box: WiredDep,
    asked: Annotated[LogAsked, Query()],
    told: Annotated[StreamTold, Header()],
) -> Response | StreamingResponse | LogPage:
    """A call's entries above the cursor: a page, or a stream that ends with the call."""
    config = await _readable(box, reading, call)
    cursor = max(asked.after, _seq_of(told.last_event_id))
    only = parse_filter(asked.types, durable=asked.durable)
    store = box.logs.store
    over = await store.sealed(call)
    if over and cursor >= await store.latest_seq(call):
        return Response(status_code=204)
    if wants_sse(told.accept):
        entries = box.logs.reading(call).stream(after=cursor, only=only)
        return streamed(_projected(entries, reading, config, ends=True), box.closing)
    read = await store.since(call, after=cursor, limit=asked.limit)
    return _page(read, only, reading, config, live=not over)


@router.get("/v1/agents/{slug}/calls", response_model=None)
async def agents_log(
    slug: str,
    reading: ReaderDep,
    box: WiredDep,
    asked: Annotated[LogAsked, Query()],
    told: Annotated[StreamTold, Header()],
) -> StreamingResponse | LogPage:
    """An agent's own log: its registrations, its declarations, its errors. It never ends."""
    if reading.acting is None:
        raise NotAllowed(NOT_YOURS)
    owner = await box.logs.store.owner(None, slug)
    if owner is not None and owner != reading.acting.org and not _the_fleet(reading):
        raise NotFound(NO_AGENT.format(slug=slug))
    config = box.registry.declared(slug)
    only = parse_filter(asked.types, durable=asked.durable)
    if wants_sse(told.accept):
        entries = box.logs.agent(slug).stream(after=asked.after, only=only)
        return streamed(_projected(entries, reading, config, ends=False), box.closing)
    read = await box.logs.store.since(f"@{slug}", after=asked.after, limit=asked.limit)
    return _page(read, only, reading, config, live=True)


@router.get("/v1/calls/{call}/state")
async def call_state(call: str, reading: ReaderDep, box: WiredDep) -> JsonObject:
    """The call's folded state as this reader may see it, and the seq a stream resumes from."""
    config = await _readable(box, reading, call)
    state = await box.logs.reading(call).snapshot()
    if state.seq == 0:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    viewer = None if reading.visit is None else reading.visit.identity
    return {
        "state": project_state(state, reading.projection, config, viewer),
        "last_seq": state.seq,
        "live": not await box.logs.store.sealed(call),
    }


@router.get("/v1/calls/{call}/recording")
async def recording(call: str, reading: ReaderDep, box: WiredDep) -> FileResponse:
    """The call's audio, with byte ranges so a player can seek."""
    await _readable(box, reading, call)
    state = await box.logs.reading(call).snapshot()
    summary = next(
        (one for one in reversed(await box.logs.store.whole(call)) if one.type == "call.summary"),
        None,
    )
    pointer = None if summary is None else summary.data.get("recording")
    if state.seq == 0 or not isinstance(pointer, str) or not pointer:
        raise NotFound(NOT_RECORDED.format(call=call))
    path = Path(pointer)
    if not await asyncio.to_thread(path.is_file):
        raise NotFound(NOT_HERE.format(call=call, path=pointer))
    return FileResponse(path, media_type="audio/ogg", filename=f"{call}.ogg")


@router.get("/v1/agents/{slug}/sessions")
async def agents_sessions(
    slug: str, reading: ReaderDep, box: WiredDep, asked: Annotated[ListAsked, Query()]
) -> SessionList:
    """The agent's newest calls, one row each."""
    wanted = index.Wanted(agent=slug, q=asked.q or None, channel=asked.channel, before=asked.before)
    return await _sessions(box, reading, wanted, asked.limit)


@router.get("/v1/sessions")
async def sessions(
    reading: ReaderDep, box: WiredDep, asked: Annotated[ListAsked, Query()]
) -> SessionList:
    """The org's newest calls across its agents, one row each."""
    wanted = index.Wanted(
        agent=asked.agent or None, q=asked.q or None, channel=asked.channel, before=asked.before
    )
    return await _sessions(box, reading, wanted, asked.limit)


# Live only, no cursor: every entry of the feed is also in a log, which is where to resume.
@router.get("/v1/events", response_model=None)
async def the_orgs_floor(reading: ReaderDep, box: WiredDep) -> StreamingResponse:
    """The org's calls and agents changing, as they change."""
    if reading.acting is None:
        raise NotAllowed(NOT_YOURS)
    feed = box.logs.feed(reading.acting.org).subscribe()
    return streamed(_projected(feed, reading, None, ends=False), box.closing)


# ── the desk ──


@router.post("/v1/calls/{call}/listen")
async def listen(call: str, key: deps.SuperviseKey, box: WiredDep) -> SeatTaken:
    """A hidden seat that hears one live call."""
    return await _seated(box, key, call, "observe")


# Not hidden: livekit delivers no track of a hidden seat, and a takeover would be silent.
@router.post("/v1/calls/{call}/supervise")
async def supervise(call: str, key: deps.SuperviseKey, box: WiredDep) -> SeatTaken:
    """A seat that speaks in one live call; its token also sends the verbs."""
    return await _seated(box, key, call, "supervise")


# The body never says who sent it: the credential does.
@router.post("/v1/calls/{call}/verbs", status_code=202)
async def verb(call: str, said: Verb, reading: ReaderDep, box: WiredDep) -> VerbTaken:
    """One supervise verb on a live call; the call's log says what it did."""
    if reading.visit is not None and reading.visit.scope != "supervise":
        raise NotAllowed(READS_ONLY)
    if reading.acting is not None:
        keys.check_opens(reading.acting.bearer, "supervise")
    await _readable(box, reading, call)
    state = await box.logs.reading(call).snapshot()
    if state.seq == 0:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    if await box.logs.store.sealed(call):
        raise Conflict(IS_OVER.format(call=call))
    served = box.live.calls.get(call)
    if served is None:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    wanted = SupervisorVerb(by=_who(reading), verb=said)
    if served.session is not None:
        await served.session.supervise(wanted)
    else:
        sent = Command(type="supervisor.verb", agent=served.agent, call=call, data=wanted.written())
        served.commands.put_nowait(sent)
    return VerbTaken(call=call, verb=said.verb, seq=None)


# ── a visitor ──


# 201, livekit's own: a client SDK's token source expects it. The ledger row is written first,
# so the dispatch spends the token once.
@router.post("/v1/tokens", status_code=201)
async def mint(said: TokenWanted, key: TalkKey, box: WiredDep) -> TokenMinted:
    """A room token for one of the key's agents, the dispatch to its world's fleet inside it."""
    _refuse_what_is_ours(said)
    corner = keys.corner_of(key.bearer, key.env)
    agent = said.agent or routes.client_named_agent(said.room_config)
    if agent is None:
        raise DeclarationRefused(NO_AGENT_NAMED)
    if box.registry.serving(corner, agent, None) is None:
        raise NotFound(NO_AGENT.format(slug=agent))
    fleet = orgs.fleet_of(await orgs.fleets(box.pool), corner.env)
    await _room_for_one_more(box, fleet, agent)
    await _admitted(box, corner, agent)
    call = new_call_id()
    ttl = min(said.ttl_s or ONE_VISIT_TTL_S, LONGEST_VISIT_TTL_S)
    expires_at = time.time() + ttl
    scope = _visit_scope(said.scope)
    visitor = said.participant_identity or f"web_{new_call_id()[5:17]}"
    dispatch = Dispatch(
        agent=agent,
        org=corner.org,
        env=corner.env,
        holder=corner.holder or None,
        scope=scope,
        caller=visitor,
        contact=said.contact,
        metadata=said.metadata,
    )
    token = keys.room_token(
        box.signer,
        call,
        scope,
        keys.Visitor(
            expires_at=expires_at,
            identity=visitor,
            dispatch=routes.room_dispatch(fleet, dispatch),
        ),
    )
    await keys.minted(box.pool, keys.Minted(call, corner.org, agent, scope, expires_at))
    return TokenMinted(
        server_url=box.settings.livekit_public_url or box.settings.livekit_url,
        participant_token=token,
        call=call,
        log_token=keys.log_token(box.signer, call, said.log),
    )


# The number is the key's own agent's in the key's world: a key issues codes for its agents.
@router.post("/v1/codes", status_code=201)
async def issue_code(said: CodeWanted, key: TalkKey, box: WiredDep) -> Code:
    """Four digits for a caller to key, the number to call, and a token that asks after them."""
    doors = await routes.of_org(box.pool, key.org, key.env)
    number = next(
        (one.number for one in doors if one.agent == said.agent and one.channel == "phone"), None
    )
    if number is None:
        raise Conflict(NO_PHONE.format(slug=said.agent, env=key.env))
    issued = await box.codes.issue(key.env, said.agent, said.ttl_s, said.log)
    # The token outlives the code a little, so the page is told it expired rather than refused.
    asks_until = issued.expires_at + A_PAGE_MAY_ASK_AFTER_S
    token = keys.code_token(box.signer, issued.code, said.agent, key.env, asks_until)
    return Code(code=issued.code, number=number, expires_at=issued.expires_at, code_token=token)


# A page holds no key: the code token its page was handed is the only credential.
@router.get("/v1/codes/{code}")
async def code_standing(
    code: str, reading: ReaderDep, box: WiredDep, asked: Annotated[CodeAsked, Query()]
) -> CodeStanding:
    """How the code stands; with ?wait=1, held up to 25 s for a call to key it."""
    visit = reading.visit
    if visit is None or visit.code != code or visit.agent is None or visit.env is None:
        raise NotAllowed(NOT_YOUR_CODE)
    issued = await box.codes.standing(visit.env, visit.agent, code)
    if issued is None:
        raise NotFound(NOBODY_ISSUED.format(code=code, agent=visit.agent))
    left = issued.expires_at - time.time()
    if asked.wait and issued.claimed is None and left > 0:
        issued = await box.codes.waited(issued, min(LONGEST_WAIT_S, left))
    if issued.claimed is not None:
        token = keys.log_token(box.signer, issued.claimed, issued.log)
        return CodeStanding(
            code=code,
            status="claimed",
            expires_at=issued.expires_at,
            call=issued.claimed,
            log_token=token,
        )
    status = "expired" if time.time() >= issued.expires_at else "waiting"
    return CodeStanding(
        code=code, status=status, expires_at=issued.expires_at, call=None, log_token=None
    )


# ── a text call ──


# The org's key, never a room token (`pinecall chat`, `test`, `simulate`). A refusal accepts the
# socket first: a close before the handshake is a bare 403 with no reason.
@router.websocket("/v1/chat")
async def chat(websocket: WebSocket) -> None:
    """One text call: {text} frames in, every entry of the call out."""
    box = deps.wired(websocket)
    try:
        held, again = await _chatting(websocket, box)
        session = (
            await _taken_up(box, held, again)
            if again
            else await open_text(box.gated, held, await _chat_context(websocket, box, held))
        )
    except PinecallError as refused:
        await websocket.accept()
        await websocket.close(code=deps.POLICY_VIOLATION, reason=deps.close_reason(str(refused)))
        return
    await websocket.accept()
    served = box.live.calls.get(session.call.context.call)
    heard = served.log.fanout.subscribe() if served is not None else None
    sending = asyncio.create_task(_sent(websocket, heard)) if heard is not None else None
    try:
        if not again:
            await session.start()
        await _turns(websocket, box, session)
    finally:
        if sending is not None:
            sending.cancel()


async def _chatting(websocket: WebSocket, box: Wired) -> tuple[Registration, str | None]:
    said = deps.bearer_of(websocket.headers)
    verified = None if said is None else await keys.verify(box.pool, said)
    if verified is None:
        raise NotAllowed(deps.TAKES_A_KEY)
    keys.check_opens(verified, "talk")
    env = keys.world_of(verified, websocket.headers.get(deps.WORLD))
    corner = keys.corner_of(verified, env)
    slug = websocket.query_params.get("agent", "")
    app = websocket.query_params.get("app")
    held = box.registry.serving(corner, slug, app)
    if held is None:
        raise NotFound(_why_not(box, corner, slug, app))
    return held, websocket.query_params.get("call")


async def _taken_up(box: Wired, held: Registration, call: str) -> Session:
    session = await resume_text(box.gated, held, call, box.settings.timezone)
    if session is None:
        raise Conflict(NOT_TAKEN_UP.format(call=call))
    return session


# A web caller is a visitor id; the org's own key may name who it is (`?contact=`) and which
# synthetic caller plays it (`?persona=`), whose rules are frozen into the call here.
async def _chat_context(websocket: WebSocket, box: Wired, held: Registration) -> CallContext:
    corner = held.corner
    named = websocket.query_params.get("persona") or None
    persona = None if named is None else await agents.persona(box.pool, corner.org, named)
    contact = websocket.query_params.get("contact")
    return CallContext(
        call=new_call_id(),
        channel=THE_WIDGET,
        direction="inbound",
        caller=websocket.query_params.get("caller") or f"web_{new_call_id()[5:17]}",
        contact=Contact(id=contact) if contact else None,
        persona=named,
        accepts_when=None if persona is None else persona.persona.accepts_when or None,
        declines_when=None if persona is None else persona.persona.declines_when or None,
        route=Route(org=corner.org, agent=held.slug, channel=THE_WIDGET, env=corner.env),
        today=today_in(box.settings.timezone),
        holder=corner.holder or None,
    )


# A failed send flips the application state; a receive after it raises RuntimeError, so the
# state is asked before each receive.
async def _turns(websocket: WebSocket, box: Wired, session: Session) -> None:
    corner = session.call.context.route
    try:
        while websocket.application_state is WebSocketState.CONNECTED:
            said = _text_of(await websocket.receive_json())
            if not said:
                continue
            try:
                await admission.admit_turn(
                    box.pool,
                    corner.org,
                    corner.env,
                    turns=session.call.turns,
                    tokens=tokens_of(session.usage),
                )
            except QuotaExhausted as refused:
                await exhausted(box.logs, corner.org, session.call.config.slug, refused)
                await text.end(session, "timeout", "platform")
                await websocket.close(
                    code=deps.POLICY_VIOLATION, reason=deps.close_reason(str(refused))
                )
                return
            await text.hears(session, said)
    except WebSocketDisconnect as gone:
        if gone.code != SERVICE_RESTART:
            await text.end(session, "caller_hung_up", "caller")


async def _sent(websocket: WebSocket, heard: Subscription) -> None:
    async for entry in heard:
        await websocket.send_json(entry.written())


def _text_of(said: Json) -> str:
    if not isinstance(said, dict):
        return ""
    text_said = said.get("text")
    return text_said if isinstance(text_said, str) else ""


# ── an agent's inbox, by contact ──

THREADS_A_PAGE = 30
# The calls merged into one thread, newest first: each costs a log read.
CALLS_IN_A_THREAD = 20
NO_THREAD = "no thread with {contact} on agent {agent} in this corner"
ONLY_WHATSAPP = (
    "a message is written on a WhatsApp thread, and {contact}'s newest call is {channel}"
)
WINDOW_CLOSED = (
    "WhatsApp's window with {contact} closed 24 h after their last message: only a template "
    "reaches them now"
)
NOTHING_OPEN = (
    "{contact}'s conversation went quiet and is sealed: their next message opens a new one"
)


class ThreadsAsked(BaseModel):
    """One page of an inbox: after the cursor of the last one, so many contacts."""

    after: str | None = None
    limit: int = Field(THREADS_A_PAGE, ge=1, le=LONGEST_LIST)


@router.get("/v1/agents/{slug}/threads")
async def threads(
    slug: str,
    key: CallsKey,
    where: CornerDep,
    box: WiredDep,
    page: Annotated[ThreadsAsked, Query()],
) -> ThreadList:
    """The agent's contacts, the one that moved last first, with what this reader has not read."""
    inbox = index.Inbox(where, slug, _reader_of(key))
    found = await index.threads(box.pool, inbox, after=page.after, limit=page.limit)
    return ThreadList(threads=[_line_of(row) for row in found.rows], next=found.next)


@router.get("/v1/agents/{slug}/threads/{contact}")
async def thread(
    slug: str, contact: str, _key: CallsKey, where: CornerDep, box: WiredDep
) -> Thread:
    """A contact's calls with the agent merged into one thread, oldest first."""
    calls = await _thread_of(box, where, slug, contact)
    facts = await index.facts_of_calls(box.pool, calls)
    messages: list[ThreadMessage] = []
    for call in reversed(calls):
        if call in facts:
            messages += _said_on(facts[call], await box.logs.store.whole(call))
    name = next((facts[call].name for call in calls if call in facts and facts[call].name), None)
    return Thread(contact=contact, name=name, messages=messages)


# The read cursor is the person's; a server's key keeps its own.
@router.post("/v1/agents/{slug}/threads/{contact}/read", status_code=204)
async def read_thread(
    slug: str, contact: str, key: CallsKey, where: CornerDep, box: WiredDep
) -> None:
    """This reader has read the thread up to now."""
    await _thread_of(box, where, slug, contact)
    await index.read(box.pool, index.Inbox(where, slug, _reader_of(key)), contact, time.time())


# A supervisor's `say` on the open conversation, sent to the contact by its reply tap. Meta lets
# a business write freely only within 24 h of the contact's last message.
@router.post("/v1/agents/{slug}/threads/{contact}/messages", status_code=202)
async def write_to(
    slug: str, contact: str, said: ThreadSay, key: TalkKey, box: WiredDep
) -> ThreadSaid:
    """Say something as the agent on the contact's open WhatsApp conversation."""
    where = keys.corner_of(key.bearer, key.env)
    newest = (await _thread_of(box, where, slug, contact))[0]
    facts = (await index.facts_of_calls(box.pool, [newest])).get(newest)
    channel = None if facts is None else facts.channel
    if facts is None or channel != "whatsapp":
        raise Conflict(ONLY_WHATSAPP.format(contact=contact, channel=channel))
    # The log's own clock, which stamped when the contact last wrote.
    if box.logs.store.clock() - max(facts.heard_at, default=0.0) > WINDOW_S:
        raise Conflict(WINDOW_CLOSED.format(contact=contact))
    served = box.live.calls.get(newest)
    if served is None or served.session is None:
        raise Conflict(NOTHING_OPEN.format(contact=contact))
    member = key.bearer.member
    by = Supervisor(id=_reader_of(key), name=None if member is None else member.name)
    await served.session.supervise(SupervisorVerb(by=by, verb=SayVerb(text=said.text)))
    return ThreadSaid(contact=contact, call=newest)


async def _thread_of(box: Wired, where: Corner, slug: str, contact: str) -> list[str]:
    calls = await index.calls_with(box.pool, where, slug, contact, limit=CALLS_IN_A_THREAD)
    if not calls:
        raise NotFound(NO_THREAD.format(contact=contact, agent=slug))
    return calls


def _reader_of(key: Acting) -> str:
    return key.bearer.key.subject or key.bearer.key.key_id


def _line_of(row: index.Thread) -> ThreadLine:
    newest = row.newest
    kind: ThreadKind = "call" if newest.spoken else ("in" if newest.last_in else "out")
    said = newest.outcome if newest.spoken else newest.last_text
    return ThreadLine(
        contact=row.contact,
        name=row.name,
        channel_last=parse_channel(newest.channel or THE_WIDGET),
        last=ThreadLast(text=said, at=row.moved_at, kind=kind),
        unread=row.unread,
        calls=row.calls,
    )


# A written call is its turns; a spoken call is one pill with its length and whether it came up.
def _said_on(facts: index.CallFacts, entries: list[Entry]) -> list[ThreadMessage]:
    channel = parse_channel(facts.channel or THE_WIDGET)
    if not facts.spoken:
        return [
            ThreadMessage(
                kind="in" if entry.type == "turn.user" else "out",
                text=str(entry.data.get("text") or ""),
                at=entry.ts,
                call=facts.call,
                channel=channel,
            )
            for entry in entries
            if entry.type in {"turn.user", "turn.agent"}
        ]
    ended = next((entry for entry in entries if entry.type == "call.ended"), None)
    lasted = None if ended is None else ended.data.get("duration_s")
    return [
        ThreadMessage(
            kind="call",
            text=facts.outcome,
            at=entries[0].ts if entries else 0.0,
            call=facts.call,
            channel=channel,
            duration_s=float(lasted) if isinstance(lasted, int | float) else None,
            answered=any(entry.type == "call.started" for entry in entries),
        )
    ]


# ── the rules every call door shares ──


# A tenant's worker opens its own org's calls in its own world; the fleet's opens any org's in
# its world, in the corner the dispatch named.
def _call_corner(key: Acting, context: CallContext) -> Corner:
    named = Corner(context.route.org, context.env, context.holder or "")
    fleet = THE_FLEET in key.bearer.key.scopes
    if not fleet and context.route.org != key.org:
        raise NotFound(NO_SUCH_CALL.format(call=context.call))
    if not fleet and context.env != key.env:
        raise NotAllowed(
            f"this key opens {key.env}, and that call's route answers in {context.env}"
        )
    return keys.corner_of(key.bearer, key.env, dispatched=named)


# An id grants nothing: another org's or world's call is the same 404 as a call nobody opened.
# The fleet's key serves every org of its world; a tenant's worker its own org alone.
def _orgs_call(box: Wired, key: Acting, call: str) -> Served:
    served = box.live.calls.get(call)
    fleet = THE_FLEET in key.bearer.key.scopes
    if served is None or served.corner.env != key.env:
        raise NotFound(NOT_OPEN.format(call=call))
    if not fleet and served.corner.org != key.org:
        raise NotFound(NOT_OPEN.format(call=call))
    return served


async def _spent(box: Wired, context: CallContext, agent: str) -> None:
    scope = context.metadata.get("scope")
    if not isinstance(scope, str):
        return
    spending = await keys.spend(box.pool, context.call)
    if spending == "spent":
        return
    sentence = (ALREADY_SPENT if spending == "already_spent" else NEVER_MINTED).format(
        call=context.call
    )
    refused = ErrorEvent(code=SPENT, message=sentence, recoverable=True)
    await box.logs.agent(agent).append("error", refused.written())
    raise Conflict(sentence)


async def _admitted(box: Wired, corner: Corner, agent: str) -> admission.Ceiling | None:
    try:
        return await admission.admit_call(
            box.pool, corner.org, corner.env, running=box.live.running(corner.org)
        )
    except QuotaExhausted as refused:
        await exhausted(box.logs, corner.org, agent, refused)
        raise


# A gateway nobody's worker reported to refuses nobody.
async def _room_for_one_more(box: Wired, fleet: str, agent: str) -> None:
    totals = box.roster.totals(fleet, time.time())
    if not totals.full:
        return
    full = FleetFull(channel=THE_WIDGET, workers=totals.workers, active=totals.active)
    await box.logs.agent(agent).append("fleet.full", full.written())
    raise NotAvailable(FLEET_FULL.format(active=totals.active, workers=totals.workers))


def _refuse_unserved(box: Wired, corner: Corner, said: Opening, held: Registration | None) -> None:
    if said.app is not None and held is None:
        raise Conflict(NOT_THAT_APP.format(app=said.app, slug=said.agent))
    # Held by consoles alone: refused before the greeting, rather than a call with no app.
    if held is None and box.registry.of(corner, said.agent) is not None:
        raise Conflict(NO_UNCLAIMED.format(slug=said.agent))


async def _tuned(
    box: Wired, corner: Corner, agent: str, held: Registration | None
) -> tuple[AgentConfig, Versions]:
    declared = box.registry.of(corner, agent) if held is None else held
    config = AgentConfig(slug=agent) if declared is None else declared.config
    return await tuned(box.pool, config, corner, await catalog.providers(box.pool))


def _why_not(box: Wired, corner: Corner, slug: str, app: str | None) -> str:
    if app is not None:
        return NOT_THAT_APP.format(app=app, slug=slug)
    if box.registry.of(corner, slug) is not None:
        return NO_UNCLAIMED.format(slug=slug)
    return f"{NO_AGENT.format(slug=slug)}: start the app that serves it, then chat again"


def _refuse_what_is_ours(said: TokenWanted) -> None:
    ours = (
        ("room_name", said.room_name),
        ("participant_name", said.participant_name),
        ("participant_metadata", said.participant_metadata),
    )
    for field, sent in ours:
        if sent is not None:
            raise DeclarationRefused(NOT_YOURS_TO_SET.format(field=field, why=OURS_TO_SET[field]))
    if any(name.startswith(OUR_ATTRIBUTES) for name in said.participant_attributes):
        raise DeclarationRefused(f"participant_attributes under {OUR_ATTRIBUTES} are minted here")


def _visit_scope(scope: str) -> keys.RoomScope:
    for visit in MINTED_FOR_A_VISIT:
        if visit == scope:
            return visit
    raise DeclarationRefused(NOT_A_VISIT.format(scopes=sorted(MINTED_FOR_A_VISIT)))


# A key reads a call of its org in its world; a token, its own call alone.
async def _readable(box: Wired, reading: Reader, call: str) -> AgentConfig | None:
    if reading.visit is not None and reading.visit.call != call:
        raise NotAllowed(NOT_YOURS)
    kept = await index.corner_of_call(box.pool, call)
    whose = None if kept is None else kept.corner
    if not _sees(reading, whose):
        raise NotFound(NO_SUCH_CALL.format(call=call))
    return None if kept is None else box.registry.declared(kept.agent)


# The org's own calls and the reader's own corner; a token is checked by its call, the fleet's
# key reads every call it serves.
def _sees(reading: Reader, whose: Corner | None) -> bool:
    if reading.visit is not None or _the_fleet(reading):
        return True
    reader = reading.corner
    if reader is None or whose is None:
        return False
    same = reader.org == whose.org and reader.env == whose.env
    return same and whose.holder in ("", reader.holder)


def _the_fleet(reading: Reader) -> bool:
    return reading.acting is not None and THE_FLEET in reading.acting.bearer.key.scopes


def _page(
    read: list[Entry], only: Filter, reading: Reader, config: AgentConfig | None, *, live: bool
) -> LogPage:
    viewer = None if reading.visit is None else reading.visit.identity
    kept = (
        project_entry(one, reading.projection, config, viewer) for one in read if only.passes(one)
    )
    # `next` is the last seq read, not kept, so a page that kept nothing still moves the cursor.
    return LogPage(
        entries=[one for one in kept if one is not None],
        live=live,
        next=read[-1].seq if read else None,
    )


async def _projected(
    entries: AsyncIterator[Entry], reading: Reader, config: AgentConfig | None, *, ends: bool
) -> AsyncIterator[str]:
    viewer = None if reading.visit is None else reading.visit.identity
    yield f"retry: {deps.RETRY_MS}\n\n"
    async for entry in paced(entries):
        if entry is None:
            yield deps.PING
            continue
        said = project_entry(entry, reading.projection, config, viewer)
        if said is not None:
            yield frame(entry.type, said, seq=entry.seq)
        if ends and entry.type == TERMINAL_EVENT:
            return


async def _commanded(waiting: asyncio.Queue[Command | None]) -> AsyncIterator[str]:
    async def taken() -> AsyncIterator[Command]:
        while (command := await waiting.get()) is not None:
            yield command

    async for command in paced(taken()):
        yield deps.PING if command is None else frame(command.type, command.written())


async def _sessions(box: Wired, reading: Reader, wanted: index.Wanted, limit: int) -> SessionList:
    if reading.acting is None or reading.corner is None:
        raise NotAllowed("a list of calls is read with a key, not a call's token")
    found = await index.found(box.pool, reading.corner, wanted, limit=limit)
    facts = await index.facts_of_calls(box.pool, found.calls)
    lines: list[SessionLine] = []
    for call in found.calls:
        state = await box.logs.reading(call).snapshot()
        if state.seq == 0:
            continue
        said = project_state(state, "tenant", box.registry.declared(state.agent or ""))
        fact = facts.get(call)
        score = None
        if fact is not None and fact.judged is not None and fact.passed is not None:
            score = SessionScore(
                held=fact.held or 0, judged=fact.judged, passed=fact.passed, reason=fact.reason
            )
        lines.append(
            SessionLine.model_validate(
                {
                    **{name: said.get(name) for name in LINE_FROM_STATE},
                    "from": said.get("from"),
                    "call": call,
                    "agent": state.agent or "",
                    "last_seq": state.seq,
                    "live": not await box.logs.store.sealed(call),
                    "score": None if score is None else score.written(),
                    "flags": [] if fact is None else fact.flags,
                }
            )
        )
    return SessionList(calls=lines, total=found.total, next=found.next)


async def _seated(box: Wired, key: Acting, call: str, scope: keys.RoomScope) -> SeatTaken:
    kept = await index.corner_of_call(box.pool, call)
    if kept is None or kept.corner is None or kept.corner.org != key.org:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    if await box.logs.store.sealed(call):
        raise Conflict(IS_OVER.format(call=call))
    seat = keys.seat(box.signer, call, scope, key.bearer)
    return SeatTaken(
        server_url=box.settings.livekit_public_url or box.settings.livekit_url,
        participant_token=seat.token,
        call=call,
        identity=seat.identity,
        org=key.org,
        subject=seat.subject,
        name=seat.name,
    )


def _who(reading: Reader) -> Supervisor:
    if reading.visit is not None:
        if reading.visit.subject is not None:
            return Supervisor(id=reading.visit.subject, name=reading.visit.name)
        return Supervisor(id=reading.visit.identity or "")
    if reading.acting is None:
        return Supervisor(id="")
    bearer = reading.acting.bearer
    if bearer.key.subject is not None:
        name = bearer.member.name if bearer.member is not None else bearer.key.name
        return Supervisor(id=bearer.key.subject, name=name)
    return Supervisor(id=A_KEY.format(org=bearer.key.org))


def _seq_of(header: str | None) -> int:
    try:
        return max(int(header or 0), 0)
    except ValueError:
        return 0
