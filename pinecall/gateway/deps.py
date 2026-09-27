"""What a request is: the box wired once, the key, its world and corner, a reader, a stream."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated

from cryptography.fernet import MultiFernet
from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from livekit import api
from starlette.requests import HTTPConnection

from pinecall.domain.errors import NotAllowed, NotSignedIn
from pinecall.domain.settings import Settings
from pinecall.domain.types import Corner, Env, JsonObject, KeyScope, parse_env
from pinecall.fleet.hub import Roster
from pinecall.gateway.live import Gated, Live, Registry
from pinecall.log.log import Logs
from pinecall.postgres.pool import Pool
from pinecall.tenancy import keys, people
from pinecall.tenancy.agents import Codes
from pinecall.tenancy.keys import PROJECTION_OF, Bearer, Signer, Visit
from pinecall.wire.parts import Projection

# The world a person's key acts in; a server's key is its own world whatever this says.
WORLD = "pinecall-env"
# An admin, in the sandbox, looking into a colleague's corner.
LOOKING_AT = "pinecall-corner"

TAKES_A_KEY = "this door takes an API key"
READ_WITH_A_KEY = "a log is read with a key, or with a token of that call"
NOT_THE_OPERATORS = "this door is the box's: its operator key, or a person the box made an operator"
NOT_WIRED = "the gateway was never wired: its lifespan never ran"


@dataclass(frozen=True)
class Wired:
    """What the gateway opened at start and every door shares, whole."""

    settings: Settings
    pool: Pool
    vault: MultiFernet
    logs: Logs
    registry: Registry
    live: Live
    roster: Roster
    codes: Codes
    signer: Signer
    # The SFU's server API, one per process.
    server: api.LiveKitAPI
    # Set when the process is told to stop: every stream ends on it.
    closing: asyncio.Event

    @property
    def gated(self) -> Gated:
        """What a written call runs through."""
        return Gated(pool=self.pool, vault=self.vault, logs=self.logs, live=self.live)


@dataclass(frozen=True)
class Acting:
    """A key as it acts in this request: the bearer and the world."""

    bearer: Bearer
    env: Env

    @property
    def org(self) -> str:
        """The key's org."""
        return self.bearer.key.org


@dataclass(frozen=True)
class Reader:
    """Who reads a log: a key in its corner, or a token of one call."""

    acting: Acting | None = None
    corner: Corner | None = None
    visit: Visit | None = None

    @property
    def projection(self) -> Projection:
        """What the reader sees of an entry: a key sees the tenant's, a token its own grant's."""
        if self.visit is None:
            return "tenant"
        return self.visit.projection or PROJECTION_OF[self.visit.scope]


def wired(connection: HTTPConnection) -> Wired:
    """The box as the lifespan wired it."""
    found: object = getattr(connection.app.state, "wired", None)
    if not isinstance(found, Wired):
        raise HTTPException(503, NOT_WIRED)
    return found


WiredDep = Annotated[Wired, Depends(wired)]


# The header only, never the URL: a URL lands in logs.
def bearer_of(headers: Mapping[str, str]) -> str | None:
    """The bearer the Authorization header carries."""
    said = headers.get("authorization", "")
    scheme, _, value = said.partition(" ")
    return (value.strip() or None) if scheme.lower() == "bearer" else None


async def bearer(connection: HTTPConnection, box: WiredDep) -> Bearer:
    """The key that knocked, and its person; 401 without saying why."""
    said = bearer_of(connection.headers)
    verified = None if said is None or keys.is_a_jwt(said) else await keys.verify(box.pool, said)
    if verified is None:
        raise NotSignedIn(TAKES_A_KEY)
    return verified


BearerDep = Annotated[Bearer, Depends(bearer)]


async def acting(connection: HTTPConnection, key: BearerDep) -> Acting:
    """The key as it acts here: a server's key in its world, a person's in the world asked."""
    return Acting(bearer=key, env=keys.world_of(key, connection.headers.get(WORLD)))


ActingDep = Annotated[Acting, Depends(acting)]


SCOPES_OF: dict[Callable[..., object], frozenset[KeyScope]] = {}


# FastAPI calls these: one per scope, each recorded so a test walks every door for exactly one.
def opening(*scopes: KeyScope) -> Callable[[Acting], Awaitable[Acting]]:
    """A dependency that lets through a key that opens one of the scopes."""

    async def opened(key: ActingDep) -> Acting:
        keys.check_opens(key.bearer, *scopes)
        return key

    SCOPES_OF[opened] = frozenset(scopes)
    return opened


AppKey = Annotated[Acting, Depends(opening("app"))]
CallsKey = Annotated[Acting, Depends(opening("calls"))]
TalkKey = Annotated[Acting, Depends(opening("talk"))]
SuperviseKey = Annotated[Acting, Depends(opening("supervise"))]
PipelineKey = Annotated[Acting, Depends(opening("pipeline"))]
WordsKey = Annotated[Acting, Depends(opening("pipeline", "words"))]
KnowledgeKey = Annotated[Acting, Depends(opening("knowledge"))]
MemoryKey = Annotated[Acting, Depends(opening("memory"))]
EvalsKey = Annotated[Acting, Depends(opening("evals"))]
UsageKey = Annotated[Acting, Depends(opening("usage"))]
FleetKey = Annotated[Acting, Depends(opening("fleet"))]
# A worker knocks with its fleet's key; a tenant's own worker with an app key.
WorkerKey = Annotated[Acting, Depends(opening("app", "fleet"))]
# The declaration is read by a worker (the fleet's key, or a tenant's own on app) and by
# whoever reads the calls it made (calls).
DeclarationKey = Annotated[Acting, Depends(opening("app", "calls", "fleet"))]


# Only the fleet's key names a corner: the corner of the call it serves.
def dispatched(
    org: Annotated[str | None, Query()] = None,
    env: Annotated[str | None, Query()] = None,
    holder: Annotated[str | None, Query()] = None,
) -> Corner | None:
    """The corner a worker asks in, from the dispatch it was started with."""
    if org is None or env is None:
        return None
    return Corner(org, parse_env(env), holder or "")


DispatchedDep = Annotated[Corner | None, Depends(dispatched)]


# Any other key acts in its own corner, or, an admin's in the sandbox, in the colleague named.
async def corner(
    connection: HTTPConnection, key: ActingDep, box: WiredDep, named: DispatchedDep
) -> Corner:
    """The corner this request acts in."""
    looked = connection.headers.get(LOOKING_AT)
    looking_at = None
    if looked and key.bearer.member is not None:
        looking_at = await people.find(box.pool, key.org, looked)
        if looking_at is None:
            raise NotAllowed(keys.NOT_A_COLLEAGUE)
    return keys.corner_of(key.bearer, key.env, looking_at=looking_at, dispatched=named)


CornerDep = Annotated[Corner, Depends(corner)]


# ?token= only for a token of ours: EventSource sets no header, and a key in a URL would leak.
async def reader(
    connection: HTTPConnection,
    box: WiredDep,
    named: DispatchedDep,
    token: Annotated[str | None, Query()] = None,
) -> Reader:
    """A key that opens the calls, or a token of one call; 401 for anything else."""
    said = bearer_of(connection.headers) or token
    if said is None:
        raise NotSignedIn(READ_WITH_A_KEY)
    if keys.is_a_jwt(said):
        visit = keys.read(box.signer, said)
        if visit is None:
            raise NotSignedIn(READ_WITH_A_KEY)
        return Reader(visit=visit)
    verified = None if said == token else await keys.verify(box.pool, said)
    if verified is None:
        raise NotSignedIn(READ_WITH_A_KEY)
    key = Acting(bearer=verified, env=keys.world_of(verified, connection.headers.get(WORLD)))
    keys.check_opens(verified, "calls")
    return Reader(acting=key, corner=await corner(connection, key, box, named))


ReaderDep = Annotated[Reader, Depends(reader)]
SCOPES_OF[reader] = frozenset({"calls"})


async def operator(connection: HTTPConnection, box: WiredDep) -> None:
    """The box's own key, or a person the box made an operator; 401 otherwise."""
    said = bearer_of(connection.headers)
    ops = box.settings.ops_key
    if said is not None and ops and compare_digest(said, ops):
        return
    verified = None if said is None else await keys.verify(box.pool, said)
    if verified is None or verified.member is None or not verified.member.operator:
        raise NotSignedIn(NOT_THE_OPERATORS)


def closing(connection: HTTPConnection) -> asyncio.Event:
    """The event set when the process is told to stop."""
    return wired(connection).closing


# ── streams ──

SSE = "text/event-stream"
# Short: a reconnect resumes losslessly from Last-Event-ID.
RETRY_MS = 1000
# Under the 30 s a proxy lets a quiet connection idle.
PING_S = 25.0
PING = ": ping\n\n"
# x-accel-buffering: nginx would hold the stream in its buffer.
SSE_HEADERS = {"cache-control": "no-store", "connection": "keep-alive", "x-accel-buffering": "no"}


def wants_sse(accept: str | None) -> bool:
    """Whether the Accept header asks for the stream rather than a page."""
    return SSE in (accept or "")


def frame(event: str, data: JsonObject, *, seq: int | None = None) -> str:
    """One SSE frame: its id (the seq), its event, its data as one line of JSON."""
    head = "" if seq is None else f"id: {seq}\n"
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def streamed(frames: AsyncIterator[str], closing: asyncio.Event) -> StreamingResponse:
    """Frames as an SSE response, ended when they run out or the process stops."""
    return StreamingResponse(until(closing, frames), media_type=SSE, headers=SSE_HEADERS)


# One pending __anext__ across pings: a new one after a ping would lose the item the first waits
# for.
async def paced[T](coming: AsyncIterator[T], every_s: float = PING_S) -> AsyncIterator[T | None]:
    """The items as they come, and None after every quiet stretch."""
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(coming))
            done, _ = await asyncio.wait({pending}, timeout=every_s)
            if not done:
                yield None
                continue
            finished, pending = pending, None
            try:
                yield finished.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None:
            pending.cancel()


async def until[T](closing: asyncio.Event, coming: AsyncIterator[T]) -> AsyncIterator[T]:
    """The items until they end or the process starts closing."""
    stopping = asyncio.ensure_future(closing.wait())
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(coming))
            await asyncio.wait({pending, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if not pending.done():
                return
            finished, pending = pending, None
            try:
                yield finished.result()
            except StopAsyncIteration:
                return
    finally:
        stopping.cancel()
        if pending is not None:
            pending.cancel()


# ── a socket's refusal ──

# RFC 6455 §5.5: a longer close reason drops the connection with no close frame.
CLOSE_REASON_BYTES = 123
POLICY_VIOLATION = 1008


def close_reason(sentence: str) -> str:
    """The sentence cut to what a close frame carries."""
    return sentence.encode("utf-8")[:CLOSE_REASON_BYTES].decode("utf-8", errors="ignore")
