"""What the suites share: a schema and a signal per test, a pool, a store, a vendor nobody ships."""

import asyncio
import json
import logging
import os
import socket
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet, MultiFernet
from fastapi import FastAPI
from livekit import api
from psycopg import sql
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as opened_socket

from pinecall.channels.offers import Offering
from pinecall.domain.names import Env, JsonObject
from pinecall.domain.org import Org
from pinecall.domain.person import KEY_SCOPES, THE_FLEET, KeyScope
from pinecall.evals.runs import Runner
from pinecall.fleet import worlds
from pinecall.fleet.roster import Roster
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import ServedCalls, Serving
from pinecall.gateway._sockets import Sockets
from pinecall.gateway.api.providers import SAMPLES_A_MINUTE
from pinecall.gateway.app import app, served_app
from pinecall.gateway.calls.threads import Threads
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.postgres.migrate import apply_migrations
from pinecall.postgres.pool import Pool, Timeouts, connect, open_pool
from pinecall.process.connections import Connections, vault_of
from pinecall.process.settings import Settings
from pinecall.process.signal import LocalSignal, RedisSignal
from pinecall.providers import catalog
from pinecall.providers.build import MODALITIES, Vendor, installed
from pinecall.providers.catalog import Providers
from pinecall.tenancy import keys, orgs, people, vault
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.knocks import Throttle
from pinecall.tenancy.mail import Outbox
from pinecall.tenancy.remembered import RememberedKeys
from pinecall.tenancy.signin import SignIns
from pinecall.tenancy.throttle import Window
from pinecall.tenancy.tokens import Signer
from pinecall.tenancy.words import Words
from pinecall.wire.frames import Entry
from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.fakes.acme import ACME
from tests.fakes.livekit import A_SECRET, Server, acme_plugin
from tests.fakes.meta import Graph, outside
from tests.fakes.twilio import Twilio

DSN = os.environ.get("DATABASE_URL", "")

postgres = pytest.mark.skipif(not DSN, reason="DATABASE_URL: a Postgres, `make test`")

REDIS = os.environ.get("PINECALL_REDIS_URL", "")

redis = pytest.mark.skipif(not REDIS, reason="PINECALL_REDIS_URL: a Redis, `make test`")

# How long a test waits for a signal's listening connection to come up.
UP_WITHIN_S = 5.0

# Tests read the clock, so it is one they can predict: 1.0, then half a second more each time.
FIRST_TICK = 1.0
A_TICK_S = 0.5


@pytest.fixture
async def schema() -> AsyncIterator[str]:
    """A schema of this test alone, with the whole schema applied, dropped at the end."""
    name = f"pinecall_test_{uuid4().hex[:12]}"
    await apply_migrations(DSN, schema=name)
    yield name
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name))
        )


@pytest.fixture
async def pool(schema: str) -> AsyncIterator[Pool]:
    """A pool on the test's schema."""
    opened = await open_pool(DSN, schema=schema, max_size=4)
    yield opened
    await opened.close()


# What is long on purpose runs past a pool's statement timeout: this pool's is short, and a lock
# held past it stands for a table of years.
A_SHORT_TIMEOUT_MS = 100


HELD_PAST_THE_TIMEOUT_S = 0.4


@pytest.fixture
async def impatient_pool(schema: str) -> AsyncIterator[Pool]:
    """A pool on the test's schema that cuts a statement and an idle transaction at 100 ms."""
    short = Timeouts(statement_ms=A_SHORT_TIMEOUT_MS, idle_in_transaction_ms=A_SHORT_TIMEOUT_MS)
    opened = await open_pool(DSN, schema=schema, max_size=2, timeouts=short)
    yield opened
    await opened.close()


async def outlasting_a_lock[T](schema: str, table: str, work: Callable[[], Awaitable[T]]) -> T:
    """Run the work while another session holds the table past the timeout; the work's result."""
    locked = asyncio.Event()

    async def locking() -> None:
        async with await connect(DSN) as connection:
            path = sql.SQL("set search_path to {}").format(sql.Identifier(schema))
            await connection.execute(path)
            async with connection.transaction():
                taken = sql.SQL("lock table {} in access exclusive mode")
                await connection.execute(taken.format(sql.Identifier(table)))
                locked.set()
                await asyncio.sleep(HELD_PAST_THE_TIMEOUT_S)

    async def once_locked() -> T:
        await locked.wait()
        return await work()

    _, result = await asyncio.gather(locking(), once_locked())
    return result


@pytest.fixture
def redis_prefix() -> str:
    """A prefix of this test alone: its channels and keys never meet another test's."""
    return f"pinecall-test-{uuid4().hex[:12]}:"


@pytest.fixture
async def redis_signal(redis_prefix: str) -> AsyncIterator[RedisSignal]:
    """A signal on the suites' Redis under the test's prefix, listening; skipped with no Redis."""
    if not REDIS:
        pytest.skip("PINECALL_REDIS_URL: a Redis, `make test`")
    signal = RedisSignal(REDIS, prefix=redis_prefix)
    signal.start()
    await came_up(signal)
    yield signal
    await signal.close()


async def came_up(signal: RedisSignal) -> None:
    """Wait until the signal's listening connection is up; the test fails past UP_WITHIN_S."""
    await asyncio.wait_for(signal.connected(), UP_WITHIN_S)


@pytest.fixture
def ticking() -> Callable[[], float]:
    """A clock that moves half a second per reading, from 1.0."""
    ticks = iter(range(10_000))
    return lambda: FIRST_TICK + A_TICK_S * next(ticks)


@pytest.fixture
async def store(pool: Pool, ticking: Callable[[], float]) -> AsyncIterator[Store]:
    """The store on the test's schema, stamping entries with the ticking clock; drained after."""
    opened = Store(pool, clock=ticking)
    yield opened
    await opened.writer.drained()


@pytest.fixture
def call() -> str:
    """A call id nobody else uses."""
    return f"CA_{uuid4().hex[:12]}"


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> str:
    """The name of a vendor installed for this test alone."""
    monkeypatch.setitem(sys.modules, f"livekit.plugins.{ACME}", acme_plugin())
    monkeypatch.setitem(installed(), ACME, Vendor(ACME, frozenset(MODALITIES)))
    return ACME


# livekit's emitter logs a listener's exception and goes on, and asyncio does the same for a
# callback: a listener of ours that breaks would pass every test while the call loses its entries.
@pytest.fixture(autouse=True)
def no_listener_fails_in_silence(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail the test when livekit or asyncio swallowed an exception of one of our listeners."""
    caplog.set_level(logging.ERROR, logger="livekit")
    caplog.set_level(logging.ERROR, logger="asyncio")
    yield
    swallowed = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name in {"livekit", "asyncio"}
    ]
    assert swallowed == []


# ── a gateway on the test's schema ──

AGENT = "clinica-norte"
LIVEKIT_KEY = "APIgateway"
FLEETS = {"production": "pinecall", "sandbox": "pinecall-sandbox"}
# The name a carrier sends the box's calls to.
BOX_DOMAIN = "box.test"
WORLDS: tuple[Env, ...] = ("production", "sandbox")


def configured(replies: list[list[str | dict[str, object]]] | None = None) -> Providers:
    """The box's configuration: every stage on acme, the model answering its script."""
    return Providers.model_validate(
        {
            "defaults": {
                "llm": {"vendor": ACME, "model": "acme-1"},
                "stt": {"vendor": ACME, "model": "acme-ears"},
                "tts": {"vendor": ACME, "model": "acme-voice"},
            },
            "tuning": {"llm/acme": {"options": {"replies": replies or []}}},
            "rates": {"acme-1": {"input": 1.0, "output": 2.0}},
        }
    )


@dataclass(frozen=True)
class Knocking:
    """The gateway as a test knocks on it: its address, the box behind it, and an org's keys."""

    url: str
    gateway: Gateway
    org: Org
    # One server key per world, and the fleet's per world.
    app: dict[Env, str]
    fleet: dict[Env, str]

    def http(self, key: str) -> httpx.AsyncClient:
        """A client that knocks with this key."""
        return httpx.AsyncClient(base_url=self.url, headers={"Authorization": f"Bearer {key}"})

    async def socket(self, path: str, key: str, world: Env | None = None) -> ClientConnection:
        """A WebSocket opened with the key in its header."""
        headers = {"Authorization": f"Bearer {key}"}
        if world is not None:
            headers["pinecall-env"] = world
        return await opened_socket(
            f"ws{self.url.removeprefix('http')}{path}", additional_headers=headers
        )


async def received(socket: ClientConnection) -> Entry:
    """The next entry a socket is sent."""
    return Entry.model_validate(json.loads(await asyncio.wait_for(socket.recv(), 5)))


async def received_until(socket: ClientConnection, kind: str) -> Entry:
    """The entries a socket is sent, until one of this kind."""
    while (entry := await received(socket)).type != kind:
        continue
    return entry


async def sent(
    socket: ClientConnection, kind: str, data: JsonObject, *, call: str | None = None
) -> None:
    """A command down an app socket."""
    frame: JsonObject = {"type": kind, "agent": AGENT, "call": call, "data": data}
    await socket.send(json.dumps(frame))


async def issued(pool: Pool, org: str, env: Env, scopes: frozenset[KeyScope]) -> str:
    """A key of the org in the world, handed back as its secret."""
    _, secret = await keys.issue(pool, keys.Issued(org=org, env=env, scopes=scopes))
    return secret


@pytest.fixture
def twilio() -> Twilio:
    """A Twilio account the box's HTTP reaches; nobody brought it yet."""
    return Twilio()


@pytest.fixture
def graph() -> Graph:
    """Meta's Graph API as the box's HTTP reaches it."""
    return Graph()


def settings_of(domain: str | None = BOX_DOMAIN) -> Settings:
    """The settings a test gateway runs on: a LiveKit nobody reaches, and the box's public name."""
    return Settings.model_validate(
        {
            "LIVEKIT_URL": "ws://127.0.0.1:9",
            "LIVEKIT_API_KEY": LIVEKIT_KEY,
            "LIVEKIT_API_SECRET": A_SECRET,
            **({"PINECALL_DOMAIN": domain} if domain else {}),
        }
    )


def _unreachable(request: httpx.Request) -> httpx.Response:
    return httpx.Response(599, text=f"nothing answers {request.url}")


@pytest.fixture
async def connections(pool: Pool) -> AsyncIterator[Connections]:
    """A process's connections on the test's pool, with nothing answering its HTTP or its SFU."""
    server = Server()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_unreachable)) as http:
        yield Connections(
            settings=settings_of(),
            pool=pool,
            writing=pool,
            vault=vault_of(Fernet.generate_key().decode()),
            http=http,
            server=server,
        )
    await server.aclose()


@dataclass(frozen=True)
class Shared:
    """What every gateway of the test's box shares: its vault, its signal, the world outside."""

    vault: MultiFernet
    signal: LocalSignal
    outside: httpx.AsyncBaseTransport


@pytest.fixture
def shared(twilio: Twilio, graph: Graph) -> Shared:
    """The box's vault, an in-process signal, and Twilio and Meta answering from fakes."""
    return Shared(
        vault=vault_of(Fernet.generate_key().decode()),
        signal=LocalSignal(),
        outside=outside(twilio, graph),
    )


@pytest.fixture
async def wired(pool: Pool, store: Store, acme: str, shared: Shared) -> AsyncIterator[Gateway]:
    """The gateway wired on the test's schema: acme on every stage, lent by the box."""
    await catalog.seed(pool, configured())
    await vault.put_box_credentials(pool, shared.vault, acme, "a key of the box")
    await worlds.set_fleets(pool, worlds.Fleets.model_validate(FLEETS))
    async with a_gateway(pool, Logs(store, shared.signal), shared) as gateway:
        yield gateway


# Another process of the same box: its own logs, writer, sockets and calls, on the same store and
# signal, the one vault.
@asynccontextmanager
async def a_gateway(pool: Pool, logs: Logs, shared: Shared) -> AsyncGenerator[Gateway]:
    """A gateway wired on these logs, its outside world answered by the transport."""
    store = logs.store
    server = Server()
    settings = settings_of()
    http = httpx.AsyncClient(transport=shared.outside)
    roster = Roster(shared.signal)
    await roster.start()
    sockets, live = Sockets(logs), ServedCalls(shared.signal)
    await sockets.start()
    await live.listen()
    connections = Connections(
        settings=settings,
        pool=pool,
        writing=pool,
        vault=shared.vault,
        http=http,
        server=server,
        signal=shared.signal,
    )
    serving = Serving(connections=connections, logs=logs, live=live, embedder=None)
    threads = Threads(serving, sockets)
    await threads.start()
    outbox = Outbox(connections, None)
    remembered = RememberedKeys(pool, shared.signal, clock=store.clock)
    await remembered.start()
    yield Gateway(
        connections=connections,
        logs=logs,
        sockets=sockets,
        live=live,
        roster=roster,
        codes=Codes(logs),
        keys=remembered,
        signer=Signer(LIVEKIT_KEY, A_SECRET),
        threads=threads,
        closing=asyncio.Event(),
        embedder=None,
        evals=Runner(pool),
        signins=SignIns.kept(Words(pool, shared.vault, store.clock)),
        outbox=outbox,
        samples=Throttle(pool, SAMPLES_A_MINUTE, store.clock),
        paced=Window(store.clock, shared.signal),
    )
    await outbox.drained()
    await remembered.close()
    await threads.closed()
    await sockets.close()
    await live.quiet()
    await roster.close()
    await store.writer.drained()
    await logs.close()
    await http.aclose()
    await server.aclose()


@pytest.fixture
async def knocking(wired: Gateway) -> AsyncIterator[Knocking]:
    """The gateway served on a free port in the test's loop, and an org with its keys."""
    org = await orgs.create(wired.connections.pool, "clinica-norte", "Clinica Norte")
    app_keys: dict[Env, str] = {
        env: await issued(wired.connections.pool, org.id, env, KEY_SCOPES) for env in WORLDS
    }
    fleet_keys: dict[Env, str] = {
        env: await issued(wired.connections.pool, "default", env, frozenset({THE_FLEET}))
        for env in WORLDS
    }
    async with served_on(app, wired) as url:
        yield Knocking(url=url, gateway=wired, org=org, app=app_keys, fleet=fleet_keys)


# A second gateway of the same box, served on a port of its own, knocked on with the same keys.
@pytest.fixture
async def knocking_two(
    knocking: Knocking, pool: Pool, store: Store, shared: Shared
) -> AsyncIterator[Knocking]:
    """The box's second gateway: the first's store and signal, its own process's memory."""
    logs = Logs(Store(pool, clock=store.clock), shared.signal)
    async with (
        a_gateway(pool, logs, shared) as second,
        served_on(served_app(), second) as url,
    ):
        yield replace(knocking, url=url, gateway=second)


@asynccontextmanager
async def served_on(served: FastAPI, gateway: Gateway) -> AsyncGenerator[str]:
    """The gateway served by the app on a free port in the test's loop; its address."""
    served.state.gateway = gateway
    # Listening before uvicorn serves: a request that arrives first waits in the backlog.
    listening = socket.create_server(("127.0.0.1", 0))
    port = listening.getsockname()[1]
    config = uvicorn.Config(served, lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[listening]))
    yield f"http://127.0.0.1:{port}"
    gateway.closing.set()
    server.should_exit = True
    await serving
    del served.state.gateway


async def a_developer(knocking: Knocking, email: str) -> tuple[str, str]:
    """A developer of the org, active: their member id and their one key."""
    invited = await people.invite(
        knocking.gateway.connections.pool,
        knocking.org.id,
        people.Invitee(email=email, name=email.split("@", maxsplit=1)[0], role="developer"),
        seats=None,
    )
    member = await people.update(
        knocking.gateway.connections.pool,
        knocking.org.id,
        invited.member.id,
        people.Change(status="active"),
    )
    _, secret = await keys.person_key(knocking.gateway.connections.pool, member)
    return member.id, secret


def a_worker_heard(
    roster: Roster, fleet: str = "pinecall-sandbox", worker: str = "w1", active: int = 0
) -> None:
    """A worker of the fleet with four seats, heard just now holding `active` calls."""
    beat = HeartbeatRequest(
        fleet=fleet,
        worker=worker,
        agent_name=f"{fleet}/{worker}",
        active=active,
        max_jobs=4,
        load=0.0,
        draining=False,
    )
    roster.report(beat, time.time())


def an_offering(pool: Pool, server: api.LiveKitAPI, fleet: str = "pinecall-sandbox") -> Offering:
    """The gateway's dispatcher on this pool and server, the fleet one worker w1 with seats free."""
    roster = Roster()
    a_worker_heard(roster, fleet)
    return Offering(pool=pool, server=server, roster=roster)
