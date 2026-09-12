"""The gateway process: the lifespan that opens what it needs, and the routers it serves."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

import httpx
from fastapi import FastAPI

from pinecall._settings import Settings, load_settings
from pinecall.api import (
    contacts,
    discovery,
    extraction,
    fleet,
    floor,
    keys,
    knowledge,
    listen,
    login,
    managed,
    members,
    numbers,
    orgs,
    pages,
    pairing,
    pipeline,
    provider_keys,
    routes,
    signup,
    supervise_seat,
    tokens,
    usage,
    whoami,
)
from pinecall.api._live import Live
from pinecall.api._refusals import refusals_answered_by
from pinecall.api.agents import dev, socket
from pinecall.api.agents import endpoints as agents
from pinecall.api.agents import provider_keys as agents_provider_keys
from pinecall.api.agents.registry import Registry
from pinecall.api.calls import chat, commands, events, listing, lookup, recording, state, tools
from pinecall.api.evals import caller, replay, runs, voice
from pinecall.api.evals.runner import Runner
from pinecall.api.supervise import verbs
from pinecall.api.whatsapp import webhook
from pinecall.api.whatsapp.threads import Threads
from pinecall.auth.codes import LoginCodes
from pinecall.auth.keys import keys_for
from pinecall.auth.members import members_for
from pinecall.auth.pairing import Pairings
from pinecall.auth.throttle import Throttle
from pinecall.evals.runs import runs_for
from pinecall.extensions import extensions_from
from pinecall.fleet import Roster
from pinecall.knowledge import PgKnowledge
from pinecall.log.snapshots import Snapshots
from pinecall.log.store import (
    MemoryStore,
    Pool,
    PostgresStore,
    Store,
    StoreUnreachable,
    migrations_behind,
    open_pool,
)
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups
from pinecall.memory import PgvectorMemory
from pinecall.orgs.admission import Admission
from pinecall.orgs.carriers import carriers_for
from pinecall.orgs.meter import Meter
from pinecall.orgs.table import orgs_for
from pinecall.orgs.turned import turned_for
from pinecall.orgs.vault import keys_brought_by, vault_for
from pinecall.providers.embed import embedder_for
from pinecall.providers.models import models_for
from pinecall.providers.overrides import Overrides
from pinecall.routes.table import routes_for
from pinecall.routes.trunks import trunks_for
from pinecall.routes.twilio import HttpTwilio
from pinecall.tokens.ledger import tokens_for
from pinecall.whatsapp.graph import HttpGraph

logger = logging.getLogger(__name__)

# What a reader is told when the log is this process's memory and nothing else. It names the URL,
# because "no database" is never the answer — the answer is always which one did not answer.
NO_DATABASE = (
    "no log will survive this process: %s did not answer (%s), so the gateway is keeping its "
    "entries in memory. Start the dev stack and run `pinecall-runtime migrate up`."
)

# A database that answered but is BEHIND. It is worse than one that did not answer: the process
# starts, every door that touches an untouched column 500s, and what a person sees is a socket
# closing with 1006 and a driver's stack trace in the log. So it is said here, once, loudly, and
# with the one command that fixes it. A box cannot meet this — its unit runs `migrate up` before
# every start — and a laptop meets it every time a migration lands, because nothing runs it there.
SCHEMA_BEHIND = (
    "this database is %d migration(s) behind (%s): the gateway will fail on any door that reads "
    "what they add. Run `pinecall-runtime migrate up` and start it again."
)


# Read and never applied: a process that migrated its own database on the way up would be a
# process that migrates it from three replicas at once. The box's unit does it before the start,
# `migrate up` does it on a laptop, and this only ever says so.
async def _say_if_the_schema_is_behind(pool: Pool) -> None:
    """One warning naming how many and which, or nothing at all when the schema is level."""
    behind = await migrations_behind(pool)
    if behind:
        logger.warning(SCHEMA_BEHIND, len(behind), ", ".join(behind))


@asynccontextmanager
async def lifespan(gateway: FastAPI) -> AsyncGenerator[None, None]:
    """Open what the process needs once, hand it to the deps on app.state, and close it after."""
    settings = load_settings()
    # First, so a box told to load a policy that is not there never answers a single request.
    gateway.state.extensions = extensions_from(settings)
    pool = await _a_pool(settings)
    if pool is not None:
        await _say_if_the_schema_is_behind(pool)
    store = await _a_store(settings)
    gateway.state.settings = settings
    gateway.state.store = store
    gateway.state.keys = keys_for(settings, pool)
    # Who the tenants are and what each may consume. A clone with no database has the default
    # org in memory and no limits, which is what a laptop with nothing up yet means.
    gateway.state.orgs = orgs_for(pool)
    # The people of every org and their invitations; the codes a key holder mints so a browser
    # logs in with no key in a URL; and how often each name has knocked with a password. The
    # last two are this process's memory on purpose: a five-minute word and a one-minute count.
    gateway.state.members = members_for(pool)
    gateway.state.login_codes = LoginCodes()
    # The words `pinecall login` prints, until a browser leaves a key in one. See api/pairing.py.
    gateway.state.pairings = Pairings()
    gateway.state.throttle = Throttle()
    # Where a tenant that brought its own provider keys keeps them. None when the box was given
    # no PINECALL_VAULT_KEY, which is every install that runs on its own vendor keys — the
    # default, and the whole of a laptop. docs/decisions/provider-keys.md.
    gateway.state.vault = vault_for(settings, pool)
    # Whose numbers reach the org's agents: the carrier a tenant brought, sealed under the same
    # vault key; the SFU's trunks the gateway admits numbers on; and how a Twilio account is
    # reached, over the process's one httpx client (opened below).
    gateway.state.carriers = carriers_for(settings, pool)
    gateway.state.trunks = trunks_for(settings)
    # Which number reaches which agent, durably. A clone with no database routes in memory: it
    # can still be told, and it forgets when the process does.
    gateway.state.routes = routes_for(pool)
    # Which call tokens were minted and which were spent: the one semantics LiveKit's token has
    # no word for. A clone with no database keeps it in memory, like the routes.
    gateway.state.tokens = tokens_for(pool)
    gateway.state.llms = models_for(settings)
    # Which calls this process is writing: what a reader subscribes to for the live half. The
    # registry writes the agents' own logs through the very same object.
    gateway.state.logs = Logs(store)
    gateway.state.registry = Registry(gateway.state.logs)
    # The gate every call and every register passes: the org's quotas against what the log says
    # it has consumed. The meter is a fold over the log and holds no table of its own.
    gateway.state.admission = Admission(gateway.state.orgs, Meter(store), gateway.state.logs)
    # It holds no registry: which socket serves a call is the door's answer, given to serve().
    gateway.state.live = Live()
    # The fleet, as its heartbeats describe it: which workers are up and what each holds. This
    # process's memory and nothing else — a restart forgets it and the next five seconds of
    # heartbeats write it again. docs/decisions/fleet.md.
    gateway.state.fleet = Roster()
    gateway.state.snapshots = Snapshots(store)
    # What an operator has turned, from the table into this process's memory: the next session
    # reads it through the very same config door a worker already asks. Read once here, so a
    # deploy does not hand every agent back the model its class declared with nobody told.
    gateway.state.overrides = Overrides(turned_for(pool))
    await gateway.state.overrides.loaded()
    # The suites: which run is happening right now, and where every run that has finished is kept.
    gateway.state.evals = Runner()
    gateway.state.eval_runs = runs_for(pool)
    # One httpx client for the life of the process, for the two services this gateway talks to
    # over HTTP: Meta's Graph API, and whichever embedder EMBED_PROVIDER names. The WhatsApp
    # conversations open right now ride beside it; none of it is durable and none of it should be.
    http = httpx.AsyncClient()
    gateway.state.graph = HttpGraph(http)
    gateway.state.twilio = partial(HttpTwilio, http)
    gateway.state.threads = Threads()
    # Memory and the knowledge base are tables, so a gateway with no pool keeps neither and says
    # so at the doors (api/_deps.py). The embedder is lazy: nothing is asked of it until a lookup
    # or a push needs a vector, so a gateway whose embedder is down still starts and the doctor's
    # line on it stays advice. One Lookups serves every text call in-process and every worker over
    # the lookup door.
    gateway.state.embedder = embedder = embedder_for(settings, http)
    gateway.state.memory = (
        None if pool is None else PgvectorMemory(pool, embedder, gateway.state.llms)
    )
    gateway.state.knowledge = None if pool is None else PgKnowledge(pool, embedder)
    gateway.state.lookups = Lookups(
        gateway.state.memory,
        gateway.state.knowledge,
        gateway.state.logs,
        gateway.state.live,
        partial(keys_brought_by, gateway.state.vault),
        gateway.state.orgs.quotas_of,
        gateway.state.admission.may_remember,
    )
    try:
        yield
    finally:
        await http.aclose()
        if isinstance(store, PostgresStore):
            await store.aclose()
        if pool is not None:
            await pool.close()


# The pool follows the URL exactly as the store does, dev key or not. A dev key means one key and no
# database REQUIRED — a clone runs before Postgres exists — and not no database ever: the day the
# dev stack is up, the same laptop has the knowledge base, a contact's memory and a durable set of
# routes, because those are tables and the tables are there. (Until 2026-09-11 a dev key refused
# the pool even with Postgres answering, and the doors it closed said "it runs on a dev key".)
async def _a_pool(settings: Settings) -> Pool | None:
    """Postgres when the URL answers, and none when it does not; the store says so out loud."""
    try:
        return await open_pool(settings.database_url)
    except StoreUnreachable:
        return None


# A clone with only a dev key still runs, and the one warning line is the whole difference between
# "the gateway is broken" and "the log is not durable yet". Refusing to start instead would make
# the first five minutes with this repo a database installation.
async def _a_store(settings: Settings) -> Store:
    """Postgres when the URL answers, and memory with one loud line when it does not."""
    try:
        return await PostgresStore.connect(settings.database_url)
    except StoreUnreachable as unreachable:
        logger.warning(NO_DATABASE, settings.database_url, unreachable)
        return MemoryStore()


app = FastAPI(title="Pinecall gateway", lifespan=lifespan)


# One door per line, in the order a reader meets them: the app's socket and what it holds, the
# calls it answers, the desk, the suites, the tenant's routes, the API keys its machines run on
# and the provider keys it brought of its own, the operator's tables under /v1/ops, the fleet's
# heartbeats and the operator's view of them,
# the tokens, Meta's webhook, the knowledge base, a contact's memory and the goldens the write
# side is held to, the org's people and the door they log in at, and whose key knocked.
for door in (
    socket.router,
    agents.router,
    agents_provider_keys.router,
    dev.router,
    events.router,
    state.router,
    listing.router,
    recording.router,
    chat.router,
    tools.router,
    commands.router,
    lookup.router,
    verbs.router,
    replay.router,
    runs.router,
    caller.router,
    voice.router,
    routes.router,
    routes.operator,
    keys.router,
    provider_keys.router,
    orgs.operator,
    provider_keys.operator,
    usage.operator,
    usage.router,
    fleet.router,
    fleet.operator,
    pipeline.router,
    tokens.router,
    listen.router,
    supervise_seat.router,
    webhook.router,
    knowledge.router,
    contacts.router,
    extraction.router,
    members.router,
    members.operator,
    login.router,
    pairing.router,
    floor.router,
    numbers.router,
    managed.router,
    signup.router,
    whoami.router,
    whoami.operator,
    discovery.router,
):
    app.include_router(door)

# The two pages, LAST and alone: the console's route is the gateway's only catch-all, and it must
# come after every door above so that no /v1 path is ever answered with a page. api/pages.py.
app.include_router(pages.router)

# What every door above answers when the embedder refuses or the rows were written by another
# model: a status and the refusal's own sentence, in one table (api/_refusals.py).
refusals_answered_by(app)
