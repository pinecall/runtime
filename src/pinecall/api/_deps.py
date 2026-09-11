"""What a request and a socket are handed: the settings, the store, the keys, the model."""

from __future__ import annotations

from hmac import compare_digest
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall._settings import Settings
from pinecall.auth.bearer import bearer_of
from pinecall.auth.keys import KeyRecord, Keys
from pinecall.evals.runs import Runs
from pinecall.knowledge import Knowledge
from pinecall.log.snapshots import Snapshots
from pinecall.log.store import Store
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.memory import Memory
from pinecall.orgs.admission import Admission
from pinecall.orgs.table import Orgs
from pinecall.orgs.vault import NO_VAULT_KEY, Vault
from pinecall.providers.embedder import Embedder
from pinecall.providers.models import Models
from pinecall.providers.overrides import Overrides
from pinecall.routes.table import Routes
from pinecall.tokens.ledger import Tokens
from pinecall.types import Org
from pinecall.whatsapp.graph import Graph


# This module holds what the PROCESS opened: every table the lifespan put on app.state, by name.
# The deps whose type is a class of this package — the registry, the live memory, the runner, the
# threads — live beside that class, because this module cannot import a door. docs/decisions/api.md.
# HTTPConnection is what a request and a WebSocket both are, so one dep serves the SSE routes and
# the app socket alike. The lifespan put these on app.state; a test overrides the function instead.
def held[T](connection: HTTPConnection, name: str, of: type[T] | None = None) -> T:
    """One thing the lifespan opened, by name. Missing means the process was never started."""
    thing: Any = getattr(connection.app.state, name, None)
    if thing is None:
        raise RuntimeError(f"the gateway has no {name}: its lifespan never ran")
    if of is not None and not isinstance(thing, of):
        raise RuntimeError(f"the gateway's {name} is a {type(thing).__name__}")
    return cast(T, thing)


def a_settings(connection: HTTPConnection) -> Settings:
    """The environment this process read at startup."""
    return held(connection, "settings", Settings)


def a_store(connection: HTTPConnection) -> Store:
    """Where entries are appended and read back. A Protocol, so isinstance says nothing here."""
    return held(connection, "store")


# Typed as Any because the two sides that ask for it want different types of the same object: the
# app socket sees a Protocol of the calls it makes (api/agents/handlers.py), the text channel
# sees the class itself. One callable, so overriding it in a test answers both.
def what_is_live(connection: HTTPConnection) -> Any:
    """The process's live memory: the app sockets open here and the calls running on them."""
    live: Any = held(connection, "live", object)
    return live


def the_keys(connection: HTTPConnection) -> Keys:
    """Where an API key is verified."""
    return held(connection, "keys")


def the_llms(connection: HTTPConnection) -> Models:
    """The way to a model, with this process's provider keys already in it."""
    return held(connection, "llms")


# The worker's doors take an API key and nothing else: no participate token reaches them, because
# nothing a browser holds may open a call's log for writing. One parser, auth/bearer.py, as every
# other door uses.
async def a_key(connection: HTTPConnection, keys: KeysDep) -> KeyRecord:
    """Whose key knocked. 401, and an unknown key is told nothing about why it is unknown."""
    bearer = bearer_of(connection.headers)
    record = None if bearer is None else await keys.verify(bearer)
    if record is None:
        raise HTTPException(401, "this door takes an API key", {"WWW-Authenticate": "Bearer"})
    return record


# A socket has no 401 to answer with, so its door asks this as a question and closes with the
# policy code on None. Both sockets — the app's and the chat's — ask here and nowhere else.
async def a_key_on_a_socket(websocket: HTTPConnection, keys: Keys) -> KeyRecord | None:
    """The key travels as the Authorization header of the upgrade, never in the URL."""
    bearer = bearer_of(websocket.headers)
    return None if bearer is None else await keys.verify(bearer)


# An operator is not a tenant: the ops key is the box's own, out of the environment, and it opens
# every /v1/ops door there will ever be. It carries no record, so it is a gate and not an identity —
# a router takes it in `dependencies=` and its endpoints never mention it.
async def an_operator(connection: HTTPConnection, settings: SettingsDep) -> None:
    """Whether the operator key knocked. An unset key closes /v1/ops, which is the safe default."""
    bearer = bearer_of(connection.headers)
    if not settings.ops_key or bearer is None or not compare_digest(bearer, settings.ops_key):
        raise HTTPException(401, "this door takes the operator key", {"WWW-Authenticate": "Bearer"})


SettingsDep = Annotated[Settings, Depends(a_settings)]
StoreDep = Annotated[Store, Depends(a_store)]
KeysDep = Annotated[Keys, Depends(the_keys)]
LlmsDep = Annotated[Models, Depends(the_llms)]
KeyDep = Annotated[KeyRecord, Depends(a_key)]


# ── the tables the lifespan opened, each behind one name ────────────────────────


def the_orgs(connection: HTTPConnection) -> Orgs:
    """The tenants this process serves. A Protocol, so isinstance says nothing here."""
    return held(connection, "orgs")


def the_routes(connection: HTTPConnection) -> Routes:
    """The table this process writes. A Protocol, so isinstance says nothing here."""
    return held(connection, "routes")


def the_tokens(connection: HTTPConnection) -> Tokens:
    """The ledger this process writes. A Protocol, so isinstance says nothing here."""
    return held(connection, "tokens")


def the_logs(connection: HTTPConnection) -> Logs:
    """The calls and agents this process is writing, so a reader can subscribe to a live one."""
    return held(connection, "logs", Logs)


def the_snapshots(connection: HTTPConnection) -> Snapshots:
    """The per-call memo of reduced states, so a hundred readers cost one reduction."""
    return held(connection, "snapshots", Snapshots)


def the_admission(connection: HTTPConnection) -> Admission:
    """The gate this process opens calls through."""
    return held(connection, "admission", Admission)


def the_overrides(connection: HTTPConnection) -> Overrides:
    """What an operator has turned since this process started."""
    return held(connection, "overrides", Overrides)


def the_runs(connection: HTTPConnection) -> Runs:
    """Where every finished eval run is kept. A Protocol, so isinstance says nothing here."""
    return held(connection, "eval_runs")


def the_graph(connection: HTTPConnection) -> Graph:
    """The one client this process talks to Meta through. A Protocol, so isinstance says nothing."""
    return held(connection, "graph")


def the_vault(connection: HTTPConnection) -> Vault | None:
    """The vault this process opened, or None when this runtime was given no vault key."""
    vault: Vault | None = getattr(connection.app.state, "vault", None)
    return vault


def the_lookups(connection: HTTPConnection) -> Lookups:
    """The gateway's answer to a turn's lookups and to a hang-up: memory and the knowledge base."""
    return held(connection, "lookups", Lookups)


# Whichever EMBED_PROVIDER named, opened once for the process and lazy: a gateway whose embedder is
# down still starts. A door asks it for one thing only — the model's name, which is what says two
# scores of one golden are comparable at all.
def the_embedder(connection: HTTPConnection) -> Embedder:
    """What memory and the knowledge base write vectors with. A Protocol, so no isinstance."""
    return held(connection, "embedder")


# Both are None on a gateway with no Postgres — a dev key — and the doors that need one say so
# in a sentence (below), while a lookup there finds nothing and refuses nobody.
def the_memory(connection: HTTPConnection) -> Memory | None:
    """The contact's facts, or None when this gateway keeps none. A Protocol: no isinstance."""
    memory: Memory | None = getattr(connection.app.state, "memory", None)
    return memory


def the_knowledge(connection: HTTPConnection) -> Knowledge | None:
    """The knowledge base, or None when this gateway keeps none. A Protocol: no isinstance."""
    knowledge: Knowledge | None = getattr(connection.app.state, "knowledge", None)
    return knowledge


OrgsDep = Annotated[Orgs, Depends(the_orgs)]
RoutesDep = Annotated[Routes, Depends(the_routes)]
TokensDep = Annotated[Tokens, Depends(the_tokens)]
LogsDep = Annotated[Logs, Depends(the_logs)]
SnapshotsDep = Annotated[Snapshots, Depends(the_snapshots)]
AdmissionDep = Annotated[Admission, Depends(the_admission)]
OverridesDep = Annotated[Overrides, Depends(the_overrides)]
RunsDep = Annotated[Runs, Depends(the_runs)]
GraphDep = Annotated[Graph, Depends(the_graph)]
VaultDep = Annotated["Vault | None", Depends(the_vault)]
LookupsDep = Annotated[Lookups, Depends(the_lookups)]
EmbedderDep = Annotated[Embedder, Depends(the_embedder)]
MemoryDep = Annotated["Memory | None", Depends(the_memory)]
KnowledgeDep = Annotated["Knowledge | None", Depends(the_knowledge)]


# The gate the operator's vault doors take, the way `an_operator` gates every /v1/ops door: asked
# before the endpoint runs, so no door has to remember to ask. The worker's own door does NOT take
# it — a runtime with no vault holds nobody's key, and an empty set is the truth there, while 503
# would end a call that was going to run on the box's own keys anyway.
async def an_unlocked_vault(vault: VaultDep) -> Vault:
    """The vault, or 503: the request was right and this box cannot honour it."""
    if vault is None:
        raise HTTPException(503, NO_VAULT_KEY)
    return vault


UnlockedVaultDep = Annotated[Vault, Depends(an_unlocked_vault)]

# A dev key opens no Postgres pool at all (api/app.py:_a_pool, "that is the whole point of it"),
# and memory and the knowledge base are tables: the request was right and this gateway cannot
# honour it. 503, in a sentence that names the cause, because a bare 503 from a push is the
# afternoon this repo already lost twice.
#
# The cause is the KEY and not the database, and this is the one place that is easy to get
# backwards — it was, on 2026-09-11, and the sentence spent an hour telling people to point
# DATABASE_URL at something. The derivation: a dev key opens no pool; a gateway with neither a dev
# key nor a database refuses to start at all (auth/keys.py:keys_for), because it could verify
# nothing. So the ONLY way to reach these two refusals is a dev key, and the way out of one is an
# org key.
NO_KNOWLEDGE = (
    "this gateway keeps no knowledge: it runs on a dev key, which opens no database at all. "
    "Issue an org key (`pinecall-runtime keys issue --org <slug>`), start the gateway on that "
    "instead of PINECALL_DEV_KEY, and push again."
)
NO_MEMORY = (
    "this gateway keeps no memory: it runs on a dev key, which opens no database at all. "
    "Issue an org key (`pinecall-runtime keys issue --org <slug>`), start the gateway on that "
    "instead of PINECALL_DEV_KEY, and read it again."
)


async def a_kept_knowledge(knowledge: KnowledgeDep) -> Knowledge:
    """The knowledge base, or 503: this gateway has no table to push into."""
    if knowledge is None:
        raise HTTPException(503, NO_KNOWLEDGE)
    return knowledge


async def a_kept_memory(memory: MemoryDep) -> Memory:
    """The contact's memory, or 503: this gateway has no table to read or forget."""
    if memory is None:
        raise HTTPException(503, NO_MEMORY)
    return memory


KeptKnowledgeDep = Annotated[Knowledge, Depends(a_kept_knowledge)]
KeptMemoryDep = Annotated[Memory, Depends(a_kept_memory)]

# Every operator door names its org, by id or by slug, and this is the one place the word becomes
# a row: a door that typed the resolution itself would be a door that could skip it. 404 for one
# nobody typed: `orgs rm` on a typo must never read as done.
NO_SUCH_ORG = "no org named {org}"


async def an_org(named: str, orgs: Orgs) -> Org:
    """The org this word names, or 404 in one sentence."""
    org = await orgs.find(named)
    if org is None:
        raise HTTPException(404, NO_SUCH_ORG.format(org=named))
    return org
