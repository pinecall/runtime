"""What a request is: the gateway it reaches, the key, its world and scope, a reader."""

import asyncio
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
    TooManyRequests,
)
from pinecall.domain.names import PRODUCTION, Env, parse_env
from pinecall.domain.person import HOLDING, THE_FLEET, THE_TEAM, KeyScope
from pinecall.domain.scope import Scope
from pinecall.gateway._call_setup import exhausted
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy import admission, keys, people, tokens
from pinecall.tenancy.keys import Bearer
from pinecall.tenancy.tokens import PROJECTION_OF, Visit
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.parts import Projection
from pinecall.wire.rest.agents import ScopeHolder

# The world a person's key acts in; a server's key is its own world whatever this says.
WORLD = "pinecall-env"

HOST = "host"


# An admin, in the sandbox, looking into a colleague's scope.
LOOKING_AT = "pinecall-corner"


TAKES_A_KEY = "this door takes an API key"


READ_WITH_A_KEY = "a log is read with a key, or with a token of that call"


NOT_THE_OPERATORS = "this door is the box's: its operator key, or a person the box made an operator"


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


def check_knock(gateway: Gateway, name: str, refusal: str) -> None:
    """Count a knock of the name; TooManyRequests with the refusal past five in a minute."""
    if not gateway.signins.throttle.allowed(name):
        raise TooManyRequests(refusal)


# The request's own Host only when it is one of the box's names: a forged host would send a
# sign-in or a password link to whoever forged it.
def public_url(request: Request, gateway: Gateway) -> str:
    """The box's name the request came in by, else production's, without a trailing slash."""
    settings = gateway.connections.settings
    world = settings.world_named(request.headers.get(HOST))
    address = settings.address_of(PRODUCTION if world is None else world)
    return address or str(request.base_url).rstrip("/")


async def admit_call(gateway: Gateway, scope: Scope, agent: str) -> admission.Ceiling | None:
    """Admit one more call of the org in its world, or write the refusal to the agent's log."""
    try:
        return await admission.admit_call(
            gateway.connections.pool,
            scope.org,
            scope.env,
            running=gateway.live.running(scope.org),
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
    verified = (
        None
        if data is None or tokens.is_a_jwt(data)
        else await keys.verify(gateway.connections.pool, data)
    )
    if verified is None:
        raise NotSignedIn(TAKES_A_KEY)
    return verified


BearerDep = Annotated[Bearer, Depends(bearer)]


async def operator(connection: HTTPConnection, gateway: GatewayDep) -> None:
    """The box's own key, or a person the box made an operator; 401 otherwise."""
    data = bearer_of(connection.headers)
    ops = gateway.connections.settings.ops_key
    if data is not None and ops and compare_digest(data, ops):
        return
    verified = None if data is None else await keys.verify(gateway.connections.pool, data)
    if verified is None or verified.member is None or not verified.member.operator:
        raise NotSignedIn(NOT_THE_OPERATORS)


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


async def acting(connection: HTTPConnection, key: BearerDep, gateway: GatewayDep) -> Acting:
    """The key as it acts here: a server's key in its world, a person's in the world asked."""
    return Acting(bearer=key, env=world_of_request(connection, key, gateway))


def world_of_request(connection: HTTPConnection, key: Bearer, gateway: Gateway) -> Env:
    """The world a request is for: the name it came in by and the header it carries, agreeing."""
    at = gateway.connections.settings.world_named(connection.headers.get(HOST))
    return keys.world_of(key, connection.headers.get(WORLD), at=at)


ActingDep = Annotated[Acting, Depends(acting)]


# The one record of who asked, which is what audits a dial and an erasure.
def asked_by(key: Acting) -> str:
    """The person behind the key, or the key itself for a server's."""
    member = key.bearer.member
    return member.id if member is not None else key.bearer.key.key_id


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
def opening(*scopes: KeyScope) -> Callable[[Acting], Awaitable[Acting]]:
    """A dependency that lets through a key that opens one of the scopes."""

    async def opened(key: ActingDep) -> Acting:
        keys.check_opens(key.bearer, *scopes)
        return key

    SCOPES_OF[opened] = frozenset(scopes)
    return opened


# The declaration is read by a worker (the fleet's key, or a tenant's own on app) and by
# whoever reads the calls it made (calls).
DeclarationKey = Annotated[Acting, Depends(opening("app", "calls", "fleet"))]


# A worker knocks with its fleet's key; a tenant's own worker with an app key.
WorkerKey = Annotated[Acting, Depends(opening("app", "fleet"))]


FleetKey = Annotated[Acting, Depends(opening("fleet"))]


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
    return keys.scope_of(key.bearer, key.env, looking_at=looking_at, dispatched=named)


ScopeDep = Annotated[Scope, Depends(scope)]


# ?token= only for a token of ours: EventSource sets no header, and a key in a URL would leak.
async def reader(
    connection: HTTPConnection,
    gateway: GatewayDep,
    named: DispatchedDep,
    token: Annotated[str | None, Query()] = None,
) -> Reader:
    """A key that opens the calls, or a token of one call; 401 for anything else."""
    data = bearer_of(connection.headers) or token
    if data is None:
        raise NotSignedIn(READ_WITH_A_KEY)
    if tokens.is_a_jwt(data):
        visit = tokens.read(gateway.signer, data)
        if visit is None:
            raise NotSignedIn(READ_WITH_A_KEY)
        return Reader(visit=visit)
    verified = None if data == token else await keys.verify(gateway.connections.pool, data)
    if verified is None:
        raise NotSignedIn(READ_WITH_A_KEY)
    key = Acting(bearer=verified, env=world_of_request(connection, verified, gateway))
    keys.check_opens(verified, "calls")
    return Reader(acting=key, scope=await scope(connection, key, gateway, named))


ReaderDep = Annotated[Reader, Depends(reader)]


SCOPES_OF[reader] = frozenset({"calls"})


def is_the_fleet(reading: Reader) -> bool:
    """Whether the reader is a fleet's key, which reads every call it serves."""
    return reading.acting is not None and THE_FLEET in reading.acting.bearer.key.scopes


# A key reads a call of its org in its world; a token, its own call alone. A call nobody wrote
# yet is empty, not another org's: a client that minted the id tails it before the room opens.
async def check_readable(gateway: Gateway, reading: Reader, call: str) -> AgentConfig | None:
    """Refuse a reader the call is not theirs; the declaration of its agent, when held."""
    if reading.visit is not None and reading.visit.call != call:
        raise NotAllowed(NOT_YOURS)
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None:
        return None
    if not _sees(reading, kept.scope):
        raise NotFound(NO_SUCH_CALL.format(call=call))
    return gateway.sockets.declared(kept.agent)


# The org's own calls and the reader's own scope; a token is checked by its call, the fleet's
# key reads every call it serves.
def _sees(reading: Reader, owner: Scope | None) -> bool:
    if reading.visit is not None or is_the_fleet(reading):
        return True
    reader = reading.scope
    if reader is None or owner is None:
        return False
    same = reader.org == owner.org and reader.env == owner.env
    return same and owner.holder in ("", reader.holder)
