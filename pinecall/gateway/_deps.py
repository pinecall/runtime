"""What a request is: the gateway it reaches, the key, its world and scope, a reader."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from starlette.requests import HTTPConnection, Request

from pinecall.domain.agent import AgentConfig
from pinecall.domain.errors import (
    NotAllowed,
    NotAvailable,
    NotFound,
    NotSignedIn,
    QuotaExhausted,
    Throttled,
    TooManyRequests,
)
from pinecall.domain.names import Env, parse_env
from pinecall.domain.person import HOLDING, THE_FLEET, THE_RUNNER, THE_TEAM, KeyScope
from pinecall.domain.scope import Scope
from pinecall.gateway._call_setup import exhausted
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy import admission, keys, people, reads, throttle, tokens
from pinecall.tenancy.keys import Bearer
from pinecall.tenancy.reads import Read
from pinecall.tenancy.tokens import PROJECTION_OF, Visit
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.parts import Projection
from pinecall.wire.rest.agents import ScopeHolder
from pinecall.wire.rest.calls import ReadKind

# The world a person's key acts in; a server's key is its own world whatever this says.
WORLD = "pinecall-env"

HOST = "host"


# An admin, in the sandbox, looking into a colleague's scope.
LOOKING_AT = "pinecall-corner"


TAKES_A_KEY = "this door takes an API key"


READ_WITH_A_KEY = "a log is read with a key, or with a token of that call"


NOT_THE_OPERATORS = "this door is the box's: its operator key, or a person the box made an operator"


# Who decided, when the box's own key did: it names no person.
OPS_KEY_NAMED = "the box's key"


NOT_STARTED = "the gateway never started: its lifespan never ran"
NO_EMBEDDER = (
    "this box embeds nothing: its providers row names no embedding, or the box holds no key for it"
)


SCOPES_OF: dict[Callable[..., object], frozenset[KeyScope]] = {}


# RFC 6455 §5.5: a longer close reason drops the connection with no close frame.
CLOSE_REASON_BYTES = 123


POLICY_VIOLATION = 1008


# A listing's page is never longer than this, whatever the reader asks.
LONGEST_LIST = 200


NO_SUCH_CALL = "no call {call} in this key's org and world"


NOT_YOURS = "this token reads another call"


NEVER_OPENED = "no call {call} was opened: the fleet's key reads and acts for a call once it is"


# How a family of doors is named: the scopes it opens, as the page of doors writes them.
FAMILY = " · "


# The platform's own keys are never paced: its workers and its runner.
UNPACED: frozenset[KeyScope] = frozenset({THE_FLEET, THE_RUNNER})


PACED = (
    "this org sent its {family} doors {limit} requests this minute, in {env}: "
    "try again in {seconds} s"
)


NOBODY_ISSUED = (
    "no code {code} is waiting for agent {agent}: nobody issued it, it expired, or a call took it"
)


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
    """Who reads a log: a key in its scope, or a token of one call."""

    acting: Acting | None = None
    scope: Scope | None = None
    visit: Visit | None = None

    @property
    def projection(self) -> Projection:
        """What the reader sees of an entry: a key sees the tenant's, a token its own grant's."""
        if self.visit is None:
            return "tenant"
        return self.visit.projection or PROJECTION_OF[self.visit.scope]


# The header only, never the URL: a URL lands in logs.
def bearer_of(headers: Mapping[str, str]) -> str | None:
    """The bearer the Authorization header carries."""
    answer = headers.get("authorization", "")
    scheme, _, value = answer.partition(" ")
    return (value.strip() or None) if scheme.lower() == "bearer" else None


# Only the fleet's key names a scope: the scope of the call it serves.
def dispatched(
    org: Annotated[str | None, Query()] = None,
    env: Annotated[str | None, Query()] = None,
    holder: Annotated[str | None, Query()] = None,
) -> Scope | None:
    """The scope a worker asks in, from the dispatch it was started with."""
    if org is None or env is None:
        return None
    return Scope(org, parse_env(env), holder or "")


DispatchedDep = Annotated[Scope | None, Depends(dispatched)]


def closing(connection: HTTPConnection) -> asyncio.Event:
    """The event set when the process is told to stop."""
    return gateway_of(connection).closing


def close_reason(sentence: str) -> str:
    """The sentence cut to what a close frame carries."""
    return sentence.encode("utf-8")[:CLOSE_REASON_BYTES].decode("utf-8", errors="ignore")


# uvicorn reads X-Forwarded-For only from the box's own Caddy (forwarded_allow_ips), so the
# client here is the person's address behind it and nobody else's word.
def client_of(request: Request) -> str:
    """The address a request came from, as the throttle counts it."""
    return request.client.host if request.client is not None else "unknown"


async def check_knock(gateway: Gateway, name: str, refusal: str) -> None:
    """Count a knock of the name; TooManyRequests with the refusal past five in a minute."""
    if not await gateway.signins.throttle.allowed(name):
        raise TooManyRequests(refusal)


# Never the request's own Host where the box has a name: a forged host would send a sign-in or
# a password link to whoever forged it.
def public_url(request: Request, gateway: Gateway) -> str:
    """The box's address, else the one the request came in by, without a trailing slash."""
    return gateway.connections.settings.address or str(request.base_url).rstrip("/")


async def admit_call(gateway: Gateway, scope: Scope, agent: str) -> admission.Ceiling | None:
    """Admit one more call of the org in its world, or write the refusal to the agent's log."""
    try:
        return await admission.admit_call(
            gateway.connections.pool,
            scope.org,
            scope.env,
            running=gateway.live.running(scope.org, scope.env),
            at=gateway.logs.store.clock(),
        )
    except QuotaExhausted as refused:
        await exhausted(gateway.logs, scope.org, agent, refused)
        raise


# A box with no embedder still starts: the doors that embed refuse, and every other answers.
def embedder_of(gateway: Gateway) -> Embedder:
    """The box's embedder; NotAvailable in one sentence when it has none."""
    if gateway.embedder is None:
        raise NotAvailable(NO_EMBEDDER)
    return gateway.embedder


def gateway_of(connection: HTTPConnection) -> Gateway:
    """The gateway as its lifespan built it."""
    found: object = getattr(connection.app.state, "gateway", None)
    if not isinstance(found, Gateway):
        raise HTTPException(503, NOT_STARTED)
    return found


GatewayDep = Annotated[Gateway, Depends(gateway_of)]


async def bearer(connection: HTTPConnection, gateway: GatewayDep) -> Bearer:
    """The key that knocked, and its person; 401 without saying why."""
    data = bearer_of(connection.headers)
    verified = None if data is None or tokens.is_a_jwt(data) else await gateway.keys.verify(data)
    if verified is None:
        raise NotSignedIn(TAKES_A_KEY)
    return verified


BearerDep = Annotated[Bearer, Depends(bearer)]


async def operator(connection: HTTPConnection, gateway: GatewayDep) -> str:
    """The box's own key, or a person the box made an operator, by name; 401 otherwise."""
    data = bearer_of(connection.headers)
    ops = gateway.connections.settings.ops_key
    if data is not None and ops and compare_digest(data, ops):
        return OPS_KEY_NAMED
    verified = None if data is None else await gateway.keys.verify(data)
    if verified is None or verified.member is None or not verified.member.operator:
        raise NotSignedIn(NOT_THE_OPERATORS)
    return verified.member.email


OperatorDep = Annotated[str, Depends(operator)]


async def named_holder(gateway: Gateway, scope: Scope) -> ScopeHolder | None:
    """Who holds the scope, by member id and email; None for the org's own."""
    if not scope.holder:
        return None
    member = await people.find(gateway.connections.pool, scope.org, scope.holder)
    return ScopeHolder(holder=scope.holder, name=None if member is None else member.email)


# A team reader with the holding scope sees every scope of the org; anyone else their own.
def sees_every_scope(key: Acting) -> bool:
    """Whether the key reads every holder's agents in its org and world."""
    return {THE_TEAM, HOLDING} <= key.bearer.key.scopes


async def acting(connection: HTTPConnection, key: BearerDep) -> Acting:
    """The key as it acts here: a server's key in its world, a person's in the world asked."""
    return Acting(bearer=key, env=world_of_request(connection, key))


def world_of_request(connection: HTTPConnection, key: Bearer) -> Env:
    """The world a request is for: the key's own, else the one its header asks for."""
    return keys.world_of(key, connection.headers.get(WORLD))


ActingDep = Annotated[Acting, Depends(acting)]


# The one record of who asked, which is what audits a dial and an erasure.
def asked_by(key: Acting) -> str:
    """The person behind the key, or the key itself for a server's."""
    member = key.bearer.member
    return member.id if member is not None else key.bearer.key.key_id


# A person's read and a server's are written down, by the person or the key; a visitor reads
# its own call and the fleet the call it serves, neither of which is an access to record.
async def record_read(gateway: Gateway, reading: Reader, call: str, what: ReadKind) -> None:
    """Write down who read the call, and what of it."""
    acting = reading.acting
    if acting is None or reading.scope is None:
        return
    await reads.record(gateway.connections.pool, reading.scope, Read(call, what, asked_by(acting)))


def ephemeral_entry(slug: str, event: WireModel, *, kind: str = "error") -> Entry:
    """An entry sent to an app socket and never stored: no call, no seq."""
    return Entry(
        seq=0,
        ts=time.time(),
        call=None,
        agent=slug,
        type=kind,
        ephemeral=True,
        data=event.written(),
    )


# FastAPI calls these: one per scope, each recorded so a test walks every door for exactly one.
def opening(*scopes: KeyScope) -> Callable[[HTTPConnection, Acting, Gateway], Awaitable[Acting]]:
    """A dependency that lets through a key that opens one of the scopes."""
    family = FAMILY.join(sorted(scopes))

    async def opened(connection: HTTPConnection, key: ActingDep, gateway: GatewayDep) -> Acting:
        keys.check_opens(key.bearer, *scopes)
        _check_agent_named(connection, key.bearer)
        if THE_FLEET not in scopes:
            await _paced(gateway, key, family)
        return key

    SCOPES_OF[opened] = frozenset(scopes)
    return opened


# The declaration is read by a worker (the fleet's key, or a tenant's own on app) and by
# whoever reads the calls it made (calls).
DeclarationKey = Annotated[Acting, Depends(opening("app", "calls", "fleet"))]


# A worker knocks with its fleet's key; a tenant's own worker with an app key.
WorkerKey = Annotated[Acting, Depends(opening("app", "fleet"))]


FleetKey = Annotated[Acting, Depends(opening("fleet"))]


# The box's runner of a world: it is told every org's hosted apps, and handed what starts them.
RunnerKey = Annotated[Acting, Depends(opening("runner"))]


NumbersKey = Annotated[Acting, Depends(opening("numbers"))]


UsageKey = Annotated[Acting, Depends(opening("usage"))]


EvalsKey = Annotated[Acting, Depends(opening("evals"))]

KnowledgeKey = Annotated[Acting, Depends(opening("knowledge"))]

MemoryKey = Annotated[Acting, Depends(opening("memory"))]


WordsKey = Annotated[Acting, Depends(opening("pipeline", "words"))]


PipelineKey = Annotated[Acting, Depends(opening("pipeline"))]


ProvidersKey = Annotated[Acting, Depends(opening("providers"))]


SuperviseKey = Annotated[Acting, Depends(opening("supervise"))]


TalkKey = Annotated[Acting, Depends(opening("talk"))]


CallsKey = Annotated[Acting, Depends(opening("calls"))]


AppKey = Annotated[Acting, Depends(opening("app"))]


TeamKey = Annotated[Acting, Depends(opening("team"))]


# Any other key acts in its own scope, or, an admin's in the sandbox, in the colleague named.
async def scope(
    connection: HTTPConnection, key: ActingDep, gateway: GatewayDep, named: DispatchedDep
) -> Scope:
    """The scope this request acts in."""
    survey = connection.headers.get(LOOKING_AT)
    looking_at = None
    if survey and key.bearer.member is not None:
        looking_at = await people.find(gateway.connections.pool, key.org, survey)
        if looking_at is None:
            raise NotAllowed(keys.NOT_A_COLLEAGUE)
    called = await _called(gateway, connection) if THE_FLEET in key.bearer.key.scopes else None
    return keys.scope_of(
        key.bearer, key.env, looking_at=looking_at, dispatched=named, called=called
    )


ScopeDep = Annotated[Scope, Depends(scope)]


# ?token= only for a token of ours: EventSource sets no header, and a key in a URL would leak.
# A worker reads the call it serves with the fleet's key and names no scope to do it, since the
# call's head row says whose it is: only a call's own reading doors (its events, its state, its
# recording) let that key in.
def reading(*opens: KeyScope) -> Callable[..., Awaitable[Reader]]:
    """A dependency that lets through a key that opens one of the scopes, or a call's token."""

    async def read(
        connection: HTTPConnection,
        gateway: GatewayDep,
        named: DispatchedDep,
        token: Annotated[str | None, Query()] = None,
    ) -> Reader:
        data = bearer_of(connection.headers) or token
        if data is None:
            raise NotSignedIn(READ_WITH_A_KEY)
        if tokens.is_a_jwt(data):
            visit = tokens.read(gateway.signer, data)
            if visit is None:
                raise NotSignedIn(READ_WITH_A_KEY)
            return Reader(visit=visit)
        verified = None if data == token else await gateway.keys.verify(data)
        if verified is None:
            raise NotSignedIn(READ_WITH_A_KEY)
        key = Acting(bearer=verified, env=world_of_request(connection, verified))
        keys.check_opens(verified, *opens)
        _check_agent_named(connection, verified)
        await _paced(gateway, key, "calls")
        if THE_FLEET in verified.key.scopes:
            return Reader(acting=key)
        return Reader(acting=key, scope=await scope(connection, key, gateway, named))

    SCOPES_OF[read] = frozenset(opens)
    return read


ReaderDep = Annotated[Reader, Depends(reading("calls"))]


CallReaderDep = Annotated[Reader, Depends(reading("calls", THE_FLEET))]


# A key reads a call of its org in its world; a token, its own call alone. A call nobody wrote
# yet is empty to a key, not another org's: a client that minted the id tails it before the room
# opens. The fleet's key reads only a call that was opened, of the world it serves.
async def check_readable(gateway: Gateway, reading: Reader, call: str) -> AgentConfig | None:
    """Refuse a reader the call is not theirs; the declaration of its agent, when held."""
    if reading.visit is not None and reading.visit.call != call:
        raise NotAllowed(NOT_YOURS)
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    fleet = reading.acting is not None and THE_FLEET in reading.acting.bearer.key.scopes
    if fleet and (kept is None or kept.scope is None):
        raise NotFound(NEVER_OPENED.format(call=call))
    if kept is None:
        return None
    if not _sees(reading, kept.scope):
        raise NotFound(NO_SUCH_CALL.format(call=call))
    if reading.acting is not None:
        keys.check_agent(reading.acting.bearer, kept.agent)
    return gateway.sockets.declared(kept.agent)


# The org's own calls and the reader's own scope; a token is checked by its call, the fleet's
# key reads the calls of the world it serves and none of the other.
def _sees(reading: Reader, owner: Scope | None) -> bool:
    if reading.visit is not None:
        return True
    if reading.acting is not None and THE_FLEET in reading.acting.bearer.key.scopes:
        return owner is not None and owner.env == reading.acting.env
    reader = reading.scope
    if reader is None or owner is None:
        return False
    same = reader.org == owner.org and reader.env == owner.env
    return same and owner.holder in ("", reader.holder)


# A worker's request that names a call, in its path or as ?call=, acts in that call's scope as its
# head row keeps it; a call nobody opened yet is refused rather than trusted.
async def _called(gateway: Gateway, connection: HTTPConnection) -> Scope | None:
    call = connection.path_params.get("call") or connection.query_params.get("call")
    if not call:
        return None
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None or kept.scope is None:
        raise NotFound(NEVER_OPENED.format(call=call))
    return kept.scope


# The agent a door names in its path or its query; a body's agent is checked by its door.
def _check_agent_named(connection: HTTPConnection, bearer: Bearer) -> None:
    for slug in (connection.path_params.get("slug"), connection.query_params.get("agent")):
        if slug:
            keys.check_agent(bearer, slug)


# One count per org, world and family: a noisy tenant waits, and nobody else does. A live call's
# own doors are never paced: admission already bounds them, and a worker reads a 4xx as final.
async def _paced(gateway: Gateway, key: Acting, family: str) -> None:
    bearer = key.bearer
    if bearer.key.scopes & UNPACED or (bearer.member is not None and bearer.member.operator):
        return
    name = f"{key.org}/{key.env}/{family}"
    wait = await gateway.paced.counted(name, throttle.REQUESTS_A_MINUTE)
    if wait is None:
        return
    seconds = max(1, math.ceil(wait))
    raise Throttled(
        PACED.format(family=family, limit=throttle.REQUESTS_A_MINUTE, env=key.env, seconds=seconds),
        retry_after_s=seconds,
    )
