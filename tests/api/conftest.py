"""One gateway per test, wired to a memory store and one issued key. Nothing reaches a network."""

from collections.abc import AsyncIterator, Iterator
from functools import partial
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.api import _deps as deps
from pinecall.api import _deps as log_writers
from pinecall.api import _deps as routes_table
from pinecall.api import _deps as tokens_ledger
from pinecall.api import _deps as whatsapp_graph
from pinecall.api import _live as gateway_connected
from pinecall.api._deps import (
    the_admission,
    the_embedder,
    the_fleet,
    the_knowledge,
    the_lookups,
    the_memory,
    the_orgs,
    the_overrides,
    the_vault,
)
from pinecall.api._live import Live
from pinecall.api.agents import registry as registry_dep
from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.api.whatsapp import threads as whatsapp_threads
from pinecall.api.whatsapp.threads import Threads
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.scopes import KEY_PROJECTION, LivekitKeys, Reader
from pinecall.fleet import Roster
from pinecall.knowledge import Knowledge
from pinecall.log.snapshots import Snapshots
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.memory import Memory
from pinecall.orgs.admission import Admission
from pinecall.orgs.meter import Meter
from pinecall.orgs.table import MemoryOrgs
from pinecall.orgs.vault import MemoryVault, Vault, keys_brought_by
from pinecall.providers.models import Chat, Models
from pinecall.providers.overrides import Overrides
from pinecall.routes.table import MemoryRoutes
from pinecall.tokens.ledger import MemoryTokens
from pinecall.types import Model, Org, ProviderKeys
from pinecall.worker.client import Gateway
from tests.api.fake_graph import FakeGraph
from tests.session.fake_llm import FakeLLM
from tests.vectors import HashEmbedder

A_KEY = "pk_test_a_key_nobody_will_ever_deploy"

# The gateway's dev key: what a clone runs on, and what the settings of this suite carry.
A_DEV_KEY = "a-dev-key-nobody-will-ever-deploy"
# The box's own key, which every /v1/ops door takes and nothing else does.
AN_OPS_KEY = "an-ops-key-nobody-will-ever-deploy"
A_RECORD = KeyRecord(key_id="k_1", org="clinica", label="ring 0")
# The tenant that key belongs to, as 0006 leaves a migrated one: its id is its own word.
AN_ORG = Org(id=A_RECORD.org, slug=A_RECORD.org, name="Clínica Norte")
A_READER = Reader(projection=KEY_PROJECTION, key=A_RECORD)
# The secret is long on purpose: pyjwt warns below 32 bytes for HS256, and this suite
# turns warnings into errors.
A_LIVEKIT = LivekitKeys(api_key="ring0key", api_secret="ring-0-signs-and-verifies-its-own-tokens")
# The box's vault key. Fernet's own generator, run once and written down: a suite that generated
# one per run would encrypt with a key no assertion could name.
A_VAULT_KEY = "Zm9yLXJpbmctMC1vbmx5LW5vYm9keS13aWxsLXVzZT0="

Json = dict[str, Any]

APPS = "/v1/apps"
CHAT = "/v1/chat"
AGENT = "clinica-norte"
# The agent's own pipeline door and the knobs of it, written once: its own package knocks at
# both, and text/ knocks at the knobs to prove a turn reaches a text call too.
PIPELINE = f"/v1/agents/{AGENT}/pipeline"
PIPELINE_KNOBS = f"{PIPELINE}/overrides"


@pytest.fixture
def store() -> MemoryStore:
    """The log this gateway writes to, readable straight from the test."""
    return MemoryStore()


@pytest.fixture
def keys() -> MemoryKeys:
    """The keys this gateway honours: one, so a second one is a refusal."""
    return MemoryKeys({A_KEY: A_RECORD})


@pytest.fixture
def orgs() -> MemoryOrgs:
    """The tenants this gateway knows: the default org, and the one the key was issued to."""
    return MemoryOrgs([AN_ORG])


@pytest.fixture
def admission(orgs: MemoryOrgs, store: MemoryStore, logs: Logs) -> Admission:
    """The gate every call and register passes, over this test's own orgs and log."""
    return Admission(orgs, Meter(store), logs)


@pytest.fixture
def settings() -> Settings:
    """The environment this gateway read: the dev key the chat door takes, and LiveKit's pair."""
    return Settings(
        dev_key=A_DEV_KEY,
        ops_key=AN_OPS_KEY,
        vault_key=A_VAULT_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
    )


@pytest.fixture
def vault(settings: Settings) -> Vault | None:
    """Where a tenant's own provider keys are kept, empty at the start of every test."""
    return MemoryVault(_a_cipher(settings))


@pytest.fixture
def snapshots(store: MemoryStore) -> Snapshots:
    """The per-call memo, empty at the start of every test so no reduction leaks between them."""
    return Snapshots(store)


@pytest.fixture
def logs(store: MemoryStore) -> Logs:
    """The logs this gateway writes: ONE table, shared by the app socket, the session and every
    reader, which is what makes an SSE reader hear a turn as it happens."""
    return Logs(store)


@pytest.fixture
def registry(logs: Logs) -> Registry:
    """The live table, empty at the start of every test. It writes the agents' own logs."""
    return Registry(logs)


@pytest.fixture
def overrides() -> Overrides:
    """What an operator has turned, empty at the start of every test: nothing is inherited."""
    return Overrides()


@pytest.fixture
def routes() -> MemoryRoutes:
    """The routes an operator typed, empty at the start of every test: nothing is inherited."""
    return MemoryRoutes()


@pytest.fixture
def tokens() -> MemoryTokens:
    """The ledger of minted call tokens, empty at the start of every test: nothing is inherited."""
    return MemoryTokens()


@pytest.fixture
def live() -> Live:
    """This process's memory: no sockets and no calls at the start of a test, and no registry."""
    return Live()


@pytest.fixture
def fleet() -> Roster:
    """The workers that have knocked: none at the start of a test, so no door refuses a room."""
    return Roster()


# None is what a gateway on a dev key holds — no Postgres, no tables — and what most of this suite
# runs on; the doors that need one override these two with a fake of the Protocol's shape.
@pytest.fixture
def memory() -> Memory | None:
    """The contact's facts this gateway keeps: none, unless a test brings a fake."""
    return None


@pytest.fixture
def knowledge() -> Knowledge | None:
    """The knowledge base this gateway keeps: none, unless a test brings a fake."""
    return None


# The one thing a door asks the embedder for is the model's name, which a golden's answer carries.
@pytest.fixture
def embedder() -> HashEmbedder:
    """The vectors this gateway would write: the suite's own, so nothing reaches TEI or a vendor."""
    return HashEmbedder()


@pytest.fixture
def lookups(
    memory: Memory | None,
    knowledge: Knowledge | None,
    logs: Logs,
    live: Live,
    vault: Vault | None,
    orgs: MemoryOrgs,
    admission: Admission,
) -> Lookups:
    """The gateway's Filler and Rememberer, over this test's tables, logs, live calls and plan."""
    return Lookups(
        memory,
        knowledge,
        logs,
        live,
        partial(keys_brought_by, vault),
        orgs.quotas_of,
        admission.may_remember,
    )


@pytest.fixture
def graph() -> FakeGraph:
    """Meta, scripted: nothing in this suite reaches graph.facebook.com."""
    return FakeGraph()


@pytest.fixture
def threads() -> Threads:
    """The WhatsApp conversations open here, none at the start of a test and none inherited."""
    return Threads()


@pytest.fixture
def llm() -> FakeLLM:
    """The model this gateway answers with: scripted, so a unit test never reaches a vendor."""
    return FakeLLM()


@pytest.fixture
def models_asked() -> list[Model | None]:
    """Every model the gateway asked the provider table for: what a session was built with."""
    return []


@pytest.fixture
def keys_asked() -> list[ProviderKeys]:
    """Whose keys the gateway asked each model to be built with: empty is the box's own."""
    return []


@pytest.fixture
def llms(llm: FakeLLM, models_asked: list[Model | None], keys_asked: list[ProviderKeys]) -> Models:
    """The provider table: what the agent declared is remembered, and the scripted model answers."""

    def ask(declared: Model | None, keys: ProviderKeys) -> Chat:
        models_asked.append(declared)
        keys_asked.append(keys)
        return llm

    return ask


# Every dependency the lifespan would have opened, answered from the fixtures instead. One fixture,
# so the three clients below share one wiring and a new dep is one line here.
@pytest.fixture
def wired(
    settings: Settings,
    snapshots: Snapshots,
    store: MemoryStore,
    keys: MemoryKeys,
    registry: Registry,
    routes: MemoryRoutes,
    tokens: MemoryTokens,
    logs: Logs,
    live: Live,
    llms: Models,
    overrides: Overrides,
    orgs: MemoryOrgs,
    vault: Vault | None,
    admission: Admission,
    graph: FakeGraph,
    threads: Threads,
    memory: Memory | None,
    knowledge: Knowledge | None,
    embedder: HashEmbedder,
    lookups: Lookups,
    fleet: Roster,
) -> Iterator[None]:
    """The real app, its deps overridden for the length of one test."""
    app.dependency_overrides[deps.a_settings] = lambda: settings
    app.dependency_overrides[deps.the_snapshots] = lambda: snapshots
    app.dependency_overrides[deps.a_store] = lambda: store
    app.dependency_overrides[deps.the_keys] = lambda: keys
    app.dependency_overrides[registry_dep.the_registry] = lambda: registry
    app.dependency_overrides[routes_table.the_routes] = lambda: routes
    app.dependency_overrides[tokens_ledger.the_tokens] = lambda: tokens
    app.dependency_overrides[log_writers.the_logs] = lambda: logs
    app.dependency_overrides[gateway_connected.what_is_live] = lambda: live
    app.dependency_overrides[deps.the_llms] = lambda: llms
    app.dependency_overrides[the_overrides] = lambda: overrides
    app.dependency_overrides[the_orgs] = lambda: orgs
    app.dependency_overrides[the_vault] = lambda: vault
    app.dependency_overrides[the_admission] = lambda: admission
    app.dependency_overrides[whatsapp_graph.the_graph] = lambda: graph
    app.dependency_overrides[whatsapp_threads.the_threads] = lambda: threads
    app.dependency_overrides[the_memory] = lambda: memory
    app.dependency_overrides[the_knowledge] = lambda: knowledge
    app.dependency_overrides[the_embedder] = lambda: embedder
    app.dependency_overrides[the_lookups] = lambda: lookups
    app.dependency_overrides[the_fleet] = lambda: fleet
    yield
    app.dependency_overrides.clear()


# The client IS entered, because two sockets of one test must share one event loop: without the
# context manager starlette gives every websocket_connect a portal of its own, and a store or a
# session shared across two loops deadlocks. Entering runs the lifespan, so the dev key is set
# first: with one, the gateway opens no Postgres pool at all, and every dep is overridden anyway.
@pytest.fixture
def gateway(wired: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: ARG001
    """A TestClient over the real ASGI app, with every dependency answered from this test."""
    monkeypatch.setenv("PINECALL_DEV_KEY", A_DEV_KEY)
    with TestClient(app) as client:
        yield client


# The worker's door, driven by the worker's own client: httpx's ASGI transport runs the real app
# in the test's loop, with no portal thread between, which is what lets a test answer a tool
# from the app's side while the worker's request is still waiting on it.
@pytest.fixture
async def worker_gateway(wired: None) -> AsyncIterator[Gateway]:  # noqa: ARG001
    """worker/client.py over the real ASGI app, knocking with the org's key."""
    http = over_the_asgi_app(f"Bearer {A_KEY}")
    yield Gateway(http)
    await http.aclose()


# The tenant's own doors — the knowledge base, a contact's memory — over the same transport, with
# the org's key: what `pinecall knowledge push` and `pinecall memory` knock with.
@pytest.fixture
async def tenant_http(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """An HTTP client on the real ASGI app, knocking with the org's key."""
    http = over_the_asgi_app(f"Bearer {A_KEY}")
    yield http
    await http.aclose()


# The operator's door, driven over the same transport for the same reason: one loop, no portal
# thread, so a test can add a route and read it back through the worker's client in one breath.
@pytest.fixture
async def ops_http(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """An HTTP client on the real ASGI app, knocking at /v1/ops with the box's ops key."""
    http = over_the_asgi_app(f"Bearer {AN_OPS_KEY}")
    yield http
    await http.aclose()


def _a_cipher(settings: Settings) -> Fernet:
    """The suite's own Fernet, built from the same field vault_for reads on a box."""
    assert settings.vault_key is not None
    return Fernet(settings.vault_key.encode())


def over_the_asgi_app(authorization: str) -> httpx.AsyncClient:
    """httpx's ASGI transport runs the real app in the test's own loop, with nothing in between."""
    transport = httpx.ASGITransport(app=app)  # pyright: ignore[reportArgumentType]
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://gateway.test",
        headers={"Authorization": authorization},
    )
