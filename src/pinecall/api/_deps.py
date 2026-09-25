"""What a request and a socket are handed: the settings, the store, the keys, the model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall._settings import Settings
from pinecall.auth.bearer import bearer_of
from pinecall.auth.codes import LoginCodes
from pinecall.auth.keys import NO_KEYS_TABLE, KeyRecord, Keys, not_opening
from pinecall.auth.members import Members
from pinecall.auth.pairing import Pairings
from pinecall.auth.signups import PendingSignups
from pinecall.auth.throttle import Throttle
from pinecall.auth.world import as_asked, as_itself, in_the_world_asked
from pinecall.evals.runs import Runs
from pinecall.extensions import Extensions
from pinecall.fleet import Roster
from pinecall.knowledge import Knowledge
from pinecall.log.snapshots import Snapshots
from pinecall.log.store import Store
from pinecall.log.store.index import CallIndex
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.memory import Memory
from pinecall.orgs.admission import Admission
from pinecall.orgs.carriers import Carriers
from pinecall.orgs.codes import Codes
from pinecall.orgs.table import Orgs
from pinecall.orgs.tuning import TuningStore
from pinecall.orgs.vault import NO_VAULT_KEY, Vault
from pinecall.providers.embedder import Embedder
from pinecall.providers.models import Models
from pinecall.routes.table import Routes
from pinecall.routes.trunks import Trunks
from pinecall.routes.twilio import TwilioFor
from pinecall.tokens.ledger import Tokens
from pinecall.types import KeyScope, Org
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


# The store folds every call's facts as it appends, so the store IS the index: one object, two
# protocols, and a test that overrides the store has overridden the index with it.
def the_call_index(store: Annotated[Store, Depends(a_store)]) -> CallIndex:
    """The questions across calls, answered off the rows the store folds (log/store/index.py)."""
    return cast(CallIndex, store)


# Typed as Any because the two sides that ask for it want different types of the same object: the
# app socket sees a Protocol of the calls it makes (api/agents/handlers.py), the text channel
# sees the class itself. One callable, so overriding it in a test answers both.
def what_is_live(connection: HTTPConnection) -> Any:
    """The process's live memory: the app sockets open here and the calls running on them."""
    live: Any = held(connection, "live", object)
    return live


def the_keys(connection: HTTPConnection) -> Keys:
    """Where an API key is verified. A gateway with no database verifies nothing, and says which."""
    keys: Keys | None = getattr(connection.app.state, "keys", None)
    if keys is None:
        raise HTTPException(503, NO_KEYS_TABLE)
    return keys


def the_llms(connection: HTTPConnection) -> Models:
    """The way to a model, with this process's provider keys already in it."""
    return held(connection, "llms")


# The worker's doors take an API key and nothing else: no participate token reaches them, because
# nothing a browser holds may open a call's log for writing. One parser, auth/bearer.py, as every
# other door uses.
async def _the_key(connection: HTTPConnection, keys: Keys) -> KeyRecord:
    """Whose key knocked. 401, and an unknown key is told nothing about why it is unknown."""
    bearer = bearer_of(connection.headers)
    record = None if bearer is None else await keys.verify(bearer)
    if record is None:
        raise HTTPException(401, "this door takes an API key", {"WWW-Authenticate": "Bearer"})
    return record


# Two readings of one key (auth/world.py), and a door takes one by the dep it declares. The doors
# that open no scope — whoami, the login code, pairing, the org switch, one's own keys — read the
# key as an IDENTITY: no production gate and no header required, because a developer the org keeps
# out of production signs in at production all the same, and must still learn who they are, mint
# the code that hands them to the sandbox and switch org, or the sandbox's login (which asks
# production who a person is) could never let them in. Every door that opens a scope reads the
# key as it ACTS in this world, gate and all (`opening`). Both refuse a header naming another
# world and a token of another, and both then read the corner an admin names (corner.py).
async def a_key(
    connection: HTTPConnection, keys: KeysDep, members: MembersDep, settings: SettingsDep
) -> KeyRecord:
    """Whose key knocked, as an identity in this instance: the bare key's read."""
    record = await _the_key(connection, keys)
    try:
        return await as_itself(record, connection.headers, members, settings)
    except PermissionError as refused:
        raise HTTPException(403, str(refused)) from refused


async def a_key_that_acts(
    connection: HTTPConnection, keys: KeysDep, members: MembersDep, settings: SettingsDep
) -> KeyRecord:
    """Whose key knocked, acting in this instance's world: what every scoped door reads."""
    record = await _the_key(connection, keys)
    try:
        return await as_asked(record, connection.headers, members, settings)
    except PermissionError as refused:
        raise HTTPException(403, str(refused)) from refused


# A socket has no 401 to answer with: its door closes with the policy code on None, and with the
# sentence of a PermissionError when the key may not open the world named. Both sockets ask here.
async def a_key_on_a_socket(
    websocket: HTTPConnection, keys: Keys, members: Members, settings: Settings
) -> KeyRecord | None:
    """The key travels as the Authorization header of the upgrade, never in the URL."""
    record = None if (bearer := bearer_of(websocket.headers)) is None else await keys.verify(bearer)
    if record is None:
        return None
    return await in_the_world_asked(record, websocket.headers, members, settings)


SettingsDep = Annotated[Settings, Depends(a_settings)]
StoreDep = Annotated[Store, Depends(a_store)]
CallIndexDep = Annotated[CallIndex, Depends(the_call_index)]
KeysDep = Annotated[Keys, Depends(the_keys)]
LlmsDep = Annotated[Models, Depends(the_llms)]
# The bare key: a door that takes it asks nothing of its scopes, and reads the key as an identity
# (above). The test over the routes names every such door; every other tenant door takes one of
# the scoped deps below, which read the key as it acts.
KeyDep = Annotated[KeyRecord, Depends(a_key)]
ActingKeyDep = Annotated[KeyRecord, Depends(a_key_that_acts)]


# One dependency per scope, and the door says which by the dep it takes: the key is verified as
# every door verifies it, then asked whether it opens THIS. 403 in the one sentence, naming what
# the key does open. The scopes ride the function as an attribute so a test can walk the app's
# routes and prove every tenant door declares exactly one — save the one door below that opens to
# two, which that test names by path so a second such door cannot arrive unnoticed.
def opening(*scopes: KeyScope) -> Callable[..., Awaitable[KeyRecord]]:
    """A dependency that hands back the key when it opens one of these scopes, else refuses."""

    async def a_key_opening(key: ActingKeyDep) -> KeyRecord:
        if (closed := not_opening(key, *scopes)) is not None:
            raise HTTPException(403, closed)
        return key

    a_key_opening.__dict__[SCOPE_OF_THE_DOOR] = frozenset(scopes)
    return a_key_opening


SCOPE_OF_THE_DOOR = "pinecall_scopes"

AppKeyDep = Annotated[KeyRecord, Depends(opening("app"))]
CallsKeyDep = Annotated[KeyRecord, Depends(opening("calls"))]
# What an agent DECLARED is read by two kinds of key: the worker holding it, which builds the
# session from it, and a reader watching its calls, which draws the state by the visibility the
# declaration gave each field. A reader's key — qa's, a supervisor's — holds no `app`, so a door
# that asked for `app` alone left every console panel at the default. The one door open to either.
DeclarationKeyDep = Annotated[KeyRecord, Depends(opening("app", "calls"))]
TalkKeyDep = Annotated[KeyRecord, Depends(opening("talk"))]
SuperviseKeyDep = Annotated[KeyRecord, Depends(opening("supervise"))]
PipelineKeyDep = Annotated[KeyRecord, Depends(opening("pipeline"))]
KnowledgeKeyDep = Annotated[KeyRecord, Depends(opening("knowledge"))]
MemoryKeyDep = Annotated[KeyRecord, Depends(opening("memory"))]
EvalsKeyDep = Annotated[KeyRecord, Depends(opening("evals"))]
ApiKeysKeyDep = Annotated[KeyRecord, Depends(opening("keys"))]
ProviderKeysKeyDep = Annotated[KeyRecord, Depends(opening("providers"))]
TeamKeyDep = Annotated[KeyRecord, Depends(opening("team"))]
UsageKeyDep = Annotated[KeyRecord, Depends(opening("usage"))]
NumbersKeyDep = Annotated[KeyRecord, Depends(opening("numbers"))]


# ── the tables the lifespan opened, each behind one name ────────────────────────


def the_extensions(connection: HTTPConnection) -> Extensions:
    """The points a policy was plugged into at startup, or the runtime's own answers."""
    return held(connection, "extensions", Extensions)


def the_orgs(connection: HTTPConnection) -> Orgs:
    """The tenants this process serves. A Protocol, so isinstance says nothing here."""
    return held(connection, "orgs")


def the_members(connection: HTTPConnection) -> Members:
    """The people of every org, and their invitations. A Protocol, so isinstance says nothing."""
    return held(connection, "members")


def the_signups(connection: HTTPConnection) -> PendingSignups:
    """The sign-ups this process is waiting on a code for."""
    return held(connection, "signups", PendingSignups)


def the_login_codes(connection: HTTPConnection) -> LoginCodes:
    """The one-use codes minted here for a browser to log in with."""
    return held(connection, "login_codes", LoginCodes)


def the_pairings(connection: HTTPConnection) -> Pairings:
    """The words a terminal printed, waiting for a browser to leave a key in one."""
    return held(connection, "pairings", Pairings)


def the_throttle(connection: HTTPConnection) -> Throttle:
    """How often each name has knocked at the password door lately."""
    return held(connection, "throttle", Throttle)


def the_routes(connection: HTTPConnection) -> Routes:
    """The table this process writes. A Protocol, so isinstance says nothing here."""
    return held(connection, "routes")


def the_codes(connection: HTTPConnection) -> Codes:
    """The codes pages show beside a number, and the call each one is waiting for."""
    return held(connection, "codes", Codes)


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


def the_tuning(connection: HTTPConnection) -> TuningStore:
    """Where an agent's tuning and the org's lexicon are kept, a version a row."""
    return held(connection, "tuning")


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


def the_carriers(connection: HTTPConnection) -> Carriers | None:
    """The org carriers, or None when this runtime was given no vault key to seal them under."""
    carriers: Carriers | None = getattr(connection.app.state, "carriers", None)
    return carriers


def the_trunks(connection: HTTPConnection) -> Trunks | None:
    """The SFU's SIP trunks, or None when this process has no LiveKit pair to reach them with."""
    trunks: Trunks | None = getattr(connection.app.state, "trunks", None)
    return trunks


def twilio_for(connection: HTTPConnection) -> TwilioFor:
    """How a tenant's Twilio account is reached: a client per set of credentials."""
    return held(connection, "twilio")


def the_fleet(connection: HTTPConnection) -> Roster:
    """Every worker that has knocked at this gateway lately, and what it holds."""
    return held(connection, "fleet", Roster)


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


ExtensionsDep = Annotated[Extensions, Depends(the_extensions)]
OrgsDep = Annotated[Orgs, Depends(the_orgs)]
MembersDep = Annotated[Members, Depends(the_members)]
LoginCodesDep = Annotated[LoginCodes, Depends(the_login_codes)]
SignupsDep = Annotated[PendingSignups, Depends(the_signups)]
PairingsDep = Annotated[Pairings, Depends(the_pairings)]
ThrottleDep = Annotated[Throttle, Depends(the_throttle)]
RoutesDep = Annotated[Routes, Depends(the_routes)]
TokensDep = Annotated[Tokens, Depends(the_tokens)]
CodesDep = Annotated[Codes, Depends(the_codes)]
LogsDep = Annotated[Logs, Depends(the_logs)]
SnapshotsDep = Annotated[Snapshots, Depends(the_snapshots)]
AdmissionDep = Annotated[Admission, Depends(the_admission)]
TuningDep = Annotated[TuningStore, Depends(the_tuning)]
RunsDep = Annotated[Runs, Depends(the_runs)]
GraphDep = Annotated[Graph, Depends(the_graph)]
VaultDep = Annotated["Vault | None", Depends(the_vault)]
LookupsDep = Annotated[Lookups, Depends(the_lookups)]
FleetDep = Annotated[Roster, Depends(the_fleet)]
CarriersDep = Annotated["Carriers | None", Depends(the_carriers)]
TrunksDep = Annotated["Trunks | None", Depends(the_trunks)]
TwilioDep = Annotated[TwilioFor, Depends(twilio_for)]
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


# A carrier's credentials are a secret exactly as a provider key is, sealed under the same vault
# key; a runtime with none cannot keep them, and says so in the vault's own sentence.
async def kept_carriers(carriers: CarriersDep) -> Carriers:
    """The carriers table, or 503: this box has no vault key."""
    if carriers is None:
        raise HTTPException(503, NO_VAULT_KEY)
    return carriers


KeptCarriersDep = Annotated[Carriers, Depends(kept_carriers)]

# Memory and the knowledge base are tables, and a gateway whose DATABASE_URL did not answer has
# none: the request was right and this gateway cannot honour it. 503, in a sentence that names the
# cause, because a bare 503 from a push is the afternoon this repo already lost twice.
#
# Until 2026-09-11 the cause was the dev key, which refused to open a pool even with Postgres up,
# and these two sentences said so. The pool follows the URL now (api/app.py:_a_pool), so the one
# way left to reach these doors closed is a database that did not answer — and the way out is the
# one app.py already prints when it happens.
NO_KNOWLEDGE = (
    "this gateway keeps no knowledge: no database answered at DATABASE_URL, so there is no table "
    "to push into. Start the dev stack, run `pinecall-runtime migrate up`, and start the gateway "
    "again."
)
NO_MEMORY = (
    "this gateway keeps no memory: no database answered at DATABASE_URL, so there is no table to "
    "read or forget. Start the dev stack, run `pinecall-runtime migrate up`, and start the gateway "
    "again."
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
