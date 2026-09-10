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
    knowledge,
    listen,
    orgs,
    pipeline,
    provider_keys,
    routes,
    supervise_seat,
    tokens,
    usage,
    whoami,
)
from pinecall.api._live import Live
from pinecall.api._refusals import refusals_answered_by
from pinecall.api.agents import endpoints as agents
from pinecall.api.agents import provider_keys as agents_provider_keys
from pinecall.api.agents import socket
from pinecall.api.agents.registry import Registry
from pinecall.api.calls import chat, commands, events, fill, listing, recording, state, tools
from pinecall.api.evals import caller, replay, runs, voice
from pinecall.api.evals.runner import Runner
from pinecall.api.supervise import verbs
from pinecall.api.whatsapp import webhook
from pinecall.api.whatsapp.threads import Threads
from pinecall.auth.keys import keys_for
from pinecall.evals.runs import runs_for
from pinecall.filling import Filling
from pinecall.knowledge import PgKnowledge
from pinecall.log.snapshots import Snapshots
from pinecall.log.store import MemoryStore, Pool, PostgresStore, Store, StoreUnreachable, open_pool
from pinecall.log.writers import Logs
from pinecall.memory import PgvectorMemory
from pinecall.orgs.admission import Admission
from pinecall.orgs.meter import Meter
from pinecall.orgs.table import orgs_for
from pinecall.orgs.vault import keys_brought_by, vault_for
from pinecall.providers.embed import embedder_for
from pinecall.providers.models import models_for
from pinecall.providers.overrides import Overrides
from pinecall.routes.table import routes_for
from pinecall.tokens.ledger import tokens_for
from pinecall.whatsapp.graph import HttpGraph

logger = logging.getLogger(__name__)

# What a reader is told when the log is this process's memory and nothing else. It names the URL,
# because "no database" is never the answer — the answer is always which one did not answer.
NO_DATABASE = (
    "no log will survive this process: %s did not answer (%s), so the gateway is keeping its "
    "entries in memory. Start the dev stack and run `pinecall-runtime migrate up`."
)


@asynccontextmanager
async def lifespan(gateway: FastAPI) -> AsyncGenerator[None, None]:
    """Open what the process needs once, hand it to the deps on app.state, and close it after."""
    settings = load_settings()
    pool = await _a_pool(settings)
    store = await _a_store(settings)
    gateway.state.settings = settings
    gateway.state.store = store
    gateway.state.keys = keys_for(settings, pool)
    # Who the tenants are and what each may consume. A clone with only a dev key has the default
    # org in memory and no limits, which is what a laptop means.
    gateway.state.orgs = orgs_for(pool)
    # Where a tenant that brought its own provider keys keeps them. None when the box was given
    # no PINECALL_VAULT_KEY, which is every install that runs on its own vendor keys — the
    # default, and the whole of a laptop. docs/decisions/provider-keys.md.
    gateway.state.vault = vault_for(settings, pool)
    # Which number reaches which agent, durably. A clone with only a dev key routes in
    # memory: it can still be told, and it forgets when the process does.
    gateway.state.routes = routes_for(pool)
    # Which call tokens were minted and which were spent: the one semantics LiveKit's token has
    # no word for. A clone with only a dev key keeps it in memory, like the routes.
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
    gateway.state.snapshots = Snapshots(store)
    # What an operator turned since this process started: the next session reads it through
    # the very same config door a worker already asks.
    gateway.state.overrides = Overrides()
    # The suites: which run is happening right now, and where every run that has finished is kept.
    gateway.state.evals = Runner()
    gateway.state.eval_runs = runs_for(pool)
    # One httpx client for the life of the process, for the two services this gateway talks to
    # over HTTP: Meta's Graph API, and whichever embedder EMBED_PROVIDER names. The WhatsApp
    # conversations open right now ride beside it; none of it is durable and none of it should be.
    http = httpx.AsyncClient()
    gateway.state.graph = HttpGraph(http)
    gateway.state.threads = Threads()
    # Memory and the knowledge base are tables, so a gateway with no pool keeps neither and says
    # so at the doors (api/_deps.py). The embedder is lazy: nothing is asked of it until a fill or
    # a push needs a vector, so a gateway whose embedder is down still starts and the doctor's
    # line on it stays advice. One Filling serves every text call in-process and every worker over
    # the fill door.
    embedder = embedder_for(settings, http)
    gateway.state.memory = (
        None if pool is None else PgvectorMemory(pool, embedder, gateway.state.llms)
    )
    gateway.state.knowledge = None if pool is None else PgKnowledge(pool, embedder)
    gateway.state.filling = Filling(
        gateway.state.memory,
        gateway.state.knowledge,
        gateway.state.logs,
        gateway.state.live,
        partial(keys_brought_by, gateway.state.vault),
    )
    try:
        yield
    finally:
        await http.aclose()
        if isinstance(store, PostgresStore):
            await store.aclose()
        if pool is not None:
            await pool.close()


async def _a_pool(settings: Settings) -> Pool | None:
    """No pool when the dev key is set: that is the whole point of it — a clone with no Postgres."""
    return None if settings.dev_key else await open_pool(settings.database_url)


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
# calls it answers, the desk, the suites, the tenant's routes and the keys it brought of its own,
# the operator's tables under /v1/ops, the tokens, Meta's webhook, the knowledge base and a
# contact's memory, and whose key knocked.
for door in (
    socket.router,
    agents.router,
    agents_provider_keys.router,
    events.router,
    state.router,
    listing.router,
    recording.router,
    chat.router,
    tools.router,
    commands.router,
    fill.router,
    verbs.router,
    replay.router,
    runs.router,
    caller.router,
    voice.router,
    routes.router,
    routes.operator,
    provider_keys.router,
    orgs.operator,
    provider_keys.operator,
    usage.operator,
    pipeline.router,
    tokens.router,
    listen.router,
    supervise_seat.router,
    webhook.router,
    knowledge.router,
    contacts.router,
    whoami.router,
):
    app.include_router(door)

# What every door above answers when the embedder refuses or the rows were written by another
# model: a status and the refusal's own sentence, in one table (api/_refusals.py).
refusals_answered_by(app)
