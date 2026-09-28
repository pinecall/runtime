"""What the suites share: a schema per test, a pool, a store, and a vendor nobody ships."""

import asyncio
import json
import logging
import os
import socket
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet
from psycopg import sql
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as opened_socket

from pinecall.domain.names import Env, JsonObject
from pinecall.domain.org import Org
from pinecall.domain.person import KEY_SCOPES, THE_FLEET, KeyScope
from pinecall.evals.runs import Runner
from pinecall.fleet import worlds
from pinecall.fleet.roster import Roster
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import ServedCalls, Serving
from pinecall.gateway._sockets import Sockets
from pinecall.gateway._threads import Threads
from pinecall.gateway.api.providers import SAMPLES_A_MINUTE
from pinecall.gateway.app import app
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.postgres.migrate import apply_migrations
from pinecall.postgres.pool import Pool, connect, open_pool
from pinecall.process.connections import Connections, vault_of
from pinecall.process.settings import Settings
from pinecall.providers import catalog
from pinecall.providers.build import MODALITIES, Vendor, installed
from pinecall.providers.catalog import Providers
from pinecall.tenancy import keys, orgs, people, vault
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.mail import Outbox
from pinecall.tenancy.signin import SignIns, Throttle
from pinecall.tenancy.tokens import Signer
from pinecall.wire.frames import Entry
from tests.fakes.acme import ACME
from tests.fakes.livekit import A_SECRET, Server, acme_plugin
from tests.fakes.meta import Graph, outside
from tests.fakes.twilio import Twilio

DSN = os.environ.get("DATABASE_URL", "")

postgres = pytest.mark.skipif(not DSN, reason="DATABASE_URL: a Postgres, `make test`")

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


@pytest.fixture
def ticking() -> Callable[[], float]:
    """A clock that moves half a second per reading, from 1.0."""
    ticks = iter(range(10_000))
    return lambda: FIRST_TICK + A_TICK_S * next(ticks)


@pytest.fixture
def store(pool: Pool, ticking: Callable[[], float]) -> Store:
    """The store on the test's schema, stamping entries with the ticking clock."""
    return Store(pool, clock=ticking)


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
            vault=vault_of(Fernet.generate_key().decode()),
            http=http,
            server=server,
        )
    await server.aclose()


@pytest.fixture
async def wired(
    pool: Pool, store: Store, acme: str, twilio: Twilio, graph: Graph
) -> AsyncIterator[Gateway]:
    """The gateway wired on the test's schema: acme on every stage, lent by the box."""
    sealed = vault_of(Fernet.generate_key().decode())
    await catalog.seed(pool, configured())
    await vault.put_box_credentials(pool, sealed, acme, "a key of the box")
    await worlds.set_fleets(pool, worlds.Fleets.model_validate(FLEETS))
    logs = Logs(store)
    server = Server()
    settings = settings_of()
    http = httpx.AsyncClient(transport=outside(twilio, graph))
    sockets, live = Sockets(logs), ServedCalls()
    connections = Connections(settings=settings, pool=pool, vault=sealed, http=http, server=server)
    serving = Serving(connections=connections, logs=logs, live=live, embedder=None)
    threads = Threads(serving, sockets)
    outbox = Outbox(connections, None)
    yield Gateway(
        connections=connections,
        logs=logs,
        sockets=sockets,
        live=live,
        roster=Roster(),
        codes=Codes(logs),
        signer=Signer(LIVEKIT_KEY, A_SECRET),
        threads=threads,
        closing=asyncio.Event(),
        embedder=None,
        evals=Runner(),
        signins=SignIns.fresh(store.clock),
        outbox=outbox,
        samples=Throttle(store.clock, tries=SAMPLES_A_MINUTE),
    )
    await outbox.drained()
    await threads.closed()
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
    app.state.gateway = wired
    # Listening before uvicorn serves: a request that arrives first waits in the backlog.
    listening = socket.create_server(("127.0.0.1", 0))
    port = listening.getsockname()[1]
    config = uvicorn.Config(app, lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[listening]))
    yield Knocking(
        url=f"http://127.0.0.1:{port}", gateway=wired, org=org, app=app_keys, fleet=fleet_keys
    )
    wired.closing.set()
    server.should_exit = True
    await serving
    del app.state.gateway


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
