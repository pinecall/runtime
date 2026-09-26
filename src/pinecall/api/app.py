"""The gateway process: the lifespan that opens what it needs, and the routers it serves."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from functools import partial

import httpx
from fastapi import FastAPI

from pinecall._settings import Settings, load_settings
from pinecall.api import pages
from pinecall.api.agents.processes import Processes
from pinecall.api.agents.registry import Registry
from pinecall.api.agents.voices import A_MINUTE_S, SAMPLES_A_MINUTE
from pinecall.api.calls.reaper import Reaper, reaping
from pinecall.api.evals.runner import Runner
from pinecall.api.live import Live
from pinecall.api.origins import AppOrigins
from pinecall.api.refusals import refusals_answered_by
from pinecall.api.routers import DOORS
from pinecall.api.telephony.sip_rebuild import reconciled
from pinecall.api.whatsapp.threads import Threads
from pinecall.api.whatsapp.waiting_loop import a_waiting_room
from pinecall.auth.keys import NO_KEYS_TABLE, keys_for
from pinecall.auth.login_codes import LoginCodes
from pinecall.auth.members import members_for
from pinecall.auth.pairing import Pairings
from pinecall.auth.signups import PendingSignups
from pinecall.auth.sso_state import Handshakes
from pinecall.auth.throttle import Throttle
from pinecall.evals.run_store import runs_for
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
from pinecall.mail import outbox_for
from pinecall.memory import PgvectorMemory
from pinecall.orgs.admission import Admission
from pinecall.orgs.box_settings import box_settings_for
from pinecall.orgs.caller_codes import Codes
from pinecall.orgs.carriers import carriers_for
from pinecall.orgs.dial_policies import dialling_for
from pinecall.orgs.hold_melody import hold_audio_for
from pinecall.orgs.meter import Meter
from pinecall.orgs.org_mail import mail_for
from pinecall.orgs.org_sso import sso_for
from pinecall.orgs.outbound_credentials import outbound_trunks_for
from pinecall.orgs.personas import personas_for
from pinecall.orgs.records import orgs_for
from pinecall.orgs.tuning_store import tuning_for
from pinecall.orgs.vault import brought_by, vault_for
from pinecall.orgs.widgets import widgets_for
from pinecall.providers.embed import embedder_for
from pinecall.providers.models import models_for
from pinecall.providers.tts.vendor_voices import Shelf
from pinecall.routes.dispatch import dispatches_for
from pinecall.routes.inbound_trunks import trunks_for
from pinecall.routes.live_rooms import rooms_for
from pinecall.routes.outbound_trunks import outbound_for
from pinecall.routes.records import routes_for
from pinecall.routes.twilio import HttpTwilio
from pinecall.tokens.ledger import tokens_for
from pinecall.whatsapp.cloud_api import HttpGraph

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


# One connect, one read, one write may each take this long on the process's HTTP client: Meta's
# Graph API, the embedder EMBED_PROVIDER names (a knowledge push embeds a base in batches), the
# voice catalogues, a peer gateway. httpx's default is five seconds for all of them, which a
# TEI on CPU embedding a batch does not keep. The OpenID and Twilio calls pass their own.
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


@asynccontextmanager
async def lifespan(gateway: FastAPI) -> AsyncGenerator[None, None]:
    """Open what the process needs once, hand it to the deps on app.state, and close it after."""
    settings = load_settings()
    # Everything opened is pushed onto the stack as it opens, so a failure halfway through the
    # start closes the pool and the client already open instead of leaking them under a traceback,
    # and the stop closes in the reverse of the order things opened.
    async with AsyncExitStack() as closing:
        await _opened(gateway, settings, closing)
        yield


async def _opened(gateway: FastAPI, settings: Settings, closing: AsyncExitStack) -> None:
    """Every collaborator on app.state, in the order they need each other."""
    # First, so a box told to load a policy that is not there never answers a single request.
    gateway.state.extensions = extensions_from(settings)
    pool = await _a_pool(settings)
    if pool is not None:
        closing.push_async_callback(pool.close)
        await _say_if_the_schema_is_behind(pool)
    store = await _a_store(settings)
    if isinstance(store, PostgresStore):
        closing.push_async_callback(store.aclose)
    gateway.state.settings = settings
    gateway.state.store = store
    # None with no database, which is a gateway that can verify nothing: said here so it is read
    # at startup and not discovered by the first request. auth/keys.py.
    gateway.state.keys = keys_for(pool)
    if gateway.state.keys is None:
        logger.error(NO_KEYS_TABLE)
    # Who the tenants are and what each may consume. A clone with no database has the default
    # org in memory and no limits, which is what a laptop with nothing up yet means.
    gateway.state.orgs = orgs_for(pool)
    # The people of every org and their invitations; the codes a key holder mints so a browser
    # logs in with no key in a URL; and how often each name has knocked with a password. The
    # last two are this process's memory on purpose: a five-minute word and a one-minute count.
    gateway.state.members = members_for(pool)
    gateway.state.login_codes = LoginCodes()
    # The sign-ups whose email has not proved itself yet: fifteen minutes each, the same memory.
    gateway.state.signups = PendingSignups()
    # The words `pinecall login` prints, until a browser leaves a key in one. See
    # api/accounts/pairing.py.
    gateway.state.pairings = Pairings()
    gateway.state.throttle = Throttle()
    # And how many voice samples each key asked for lately: a vendor's seconds, on somebody's
    # account, with no usage row to count them (api/agents/voices.py).
    gateway.state.sampling = Throttle(SAMPLES_A_MINUTE, A_MINUTE_S)
    # The sign-ins out at an identity provider right now: a state, a nonce and a PKCE verifier
    # per person between the redirect and the callback. This process's memory, like the two
    # above, and for the same reason: a ten-minute word does not need a table (auth/sso_state.py).
    gateway.state.handshakes = Handshakes()
    # Where a tenant that brought its own provider keys keeps them. None when the box was given
    # no PINECALL_VAULT_KEY, which is every install that runs on its own vendor keys — the
    # default, and the whole of a laptop. docs/decisions/provider-keys.md.
    gateway.state.vault = vault_for(settings, pool)
    # Where an org's people prove who they are, when it is not this box: the OpenID client it is
    # at its own IdP, its secret sealed under the same vault key — and so None, and the doors
    # 503, on a box that was given none. orgs/org_sso.py.
    gateway.state.sso = sso_for(settings, pool)
    # The one place a letter leaves by: the account an org wired of its own, sealed under the same
    # vault key (orgs/org_mail.py); the box's PINECALL_SMTP_URL when it wired none; nobody with
    # neither. What the operator configured for the box itself from the console — its brand, its own
    # mail, a box-wide "Continue with Google" — one row a setting, secrets under the same vault key
    # (orgs/box_settings.py). It exists without one: the brand is no secret.
    gateway.state.box_settings = box_settings_for(settings, pool)
    gateway.state.outbox = outbox_for(
        settings, mail_for(settings, pool), gateway.state.box_settings
    )
    # Whose numbers reach the org's agents: the carrier a tenant brought, sealed under the same
    # vault key; the SFU's trunks the gateway admits numbers on; and how a Twilio account is
    # reached, over the process's one httpx client (opened below).
    gateway.state.carriers = carriers_for(settings, pool)
    gateway.state.trunks = trunks_for(settings)
    # And the other direction: the trunk the org places a call THROUGH, sealed under the same key
    # because the password on it is one this box minted and can read back from nowhere else; the
    # SFU's outbound side; and how a job is started on a call nobody rang.
    gateway.state.outbound_trunks = outbound_trunks_for(settings, pool)
    gateway.state.outbound = outbound_for(settings)
    gateway.state.dispatches = dispatches_for(settings)
    # What each org may dial and what it has dialled: the guards, and the ledger they count from.
    gateway.state.dial_policies, gateway.state.dials = dialling_for(pool)
    # Which number reaches which agent, durably. A clone with no database routes in memory: it
    # can still be told, and it forgets when the process does.
    gateway.state.routes = routes_for(pool)
    # How the widget presents each agent: what the console sets and the snippet it copies reads.
    gateway.state.widgets = widgets_for(pool)
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
    # The app sockets open right now, one process each: where it runs, and its stop (GET /v1/apps).
    gateway.state.processes = Processes()
    # The fleet, as its heartbeats describe it: which workers are up and what each holds. This
    # process's memory and nothing else — a restart forgets it and the next five seconds of
    # heartbeats write it again. docs/decisions/fleet.md.
    gateway.state.fleet = Roster()
    gateway.state.snapshots = Snapshots(store)
    # What the org set over every agent's class, per world, per corner, a version a row. Read per
    # session and never cached: what one gateway sets is on the next call of every other one.
    gateway.state.tuning = tuning_for(pool)
    # The org's synthetic callers, written from the console or the CLI and played by a model.
    gateway.state.personas = personas_for(pool)
    # Which melody each agent plays while a tool runs, read per call: the table is small.
    gateway.state.hold_audio = hold_audio_for(pool)
    # The suites: which run is happening right now, and where every run that has finished is kept.
    gateway.state.evals = Runner()
    gateway.state.eval_runs = runs_for(pool)
    # One httpx client for the life of the process, for the two services this gateway talks to
    # over HTTP: Meta's Graph API, and whichever embedder EMBED_PROVIDER names. The WhatsApp
    # conversations open right now ride beside it; none of it is durable and none of it should be.
    http = await closing.enter_async_context(httpx.AsyncClient(timeout=HTTP_TIMEOUT))
    # Named on the state as well, because a third caller rides it now: the sign-in that asks an
    # org's identity provider for its configuration, its keys and one token
    # (api/accounts/sso_login.py).
    gateway.state.http = http
    gateway.state.graph = HttpGraph(http)
    # The voice vendors' own catalogues, which a person picks a voice from (api/agents/voices.py).
    gateway.state.shelf = Shelf(http)
    gateway.state.twilio = partial(HttpTwilio, http)
    gateway.state.threads = Threads()
    # The codes pages show beside a phone number, kept on each agent's log and read back here, so
    # a page waiting through a restart is still answered when its caller keys the code.
    gateway.state.codes = Codes(gateway.state.logs)
    await gateway.state.codes.loaded(store)
    # Memory and the knowledge base are tables, so a gateway with no pool keeps neither and says
    # so at the doors (api/deps.py). The embedder is lazy: nothing is asked of it until a lookup
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
        partial(brought_by, gateway.state.vault, gateway.state.orgs.quotas_of),
        gateway.state.orgs.quotas_of,
        gateway.state.admission.may_remember,
    )
    # The one thing this process does with nobody asking. A spoken call is ended by the worker
    # holding it, so a worker that is killed leaves a log that nothing on earth would ever close:
    # api/calls/reaper.py. It needs the SFU to tell a dead call from a quiet one, so a gateway with
    # no LiveKit pair runs none — and one with no pair has no spoken call to reap either.
    reaper = _a_reaper(settings, gateway)
    if reaper is not None:
        closing.push_async_callback(_cancelled, reaper)
    # And the one thing it does for the media plane: ask it, once, for every trunk the tables say
    # exists. A Redis that came up empty took every number with it and nothing said so
    # (2026-09-22); this is what says so, and puts them back. api/telephony/sip_rebuild.py.
    rebuilding = _a_rebuild(gateway)
    if rebuilding is not None:
        closing.push_async_callback(_cancelled, rebuilding)
    # And the WhatsApp messages that reached a number while nobody held its agent — a deploy, this
    # very restart — are kept on the log and answered once somebody does:
    # api/whatsapp/unanswered.py.
    closing.push_async_callback(_cancelled, await a_waiting_room(gateway.state))


NO_REAPER = (
    "no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: this gateway cannot ask the SFU which calls are "
    "still running, so a spoken call whose worker dies will stay open until somebody seals it"
)


# The store IS the call index (api/deps.py), which is why one object answers both here.
def _a_reaper(settings: Settings, gateway: FastAPI) -> asyncio.Task[None] | None:
    """The reaper's loop, started; None and one line when this process has no SFU to ask."""
    rooms = rooms_for(settings)
    if rooms is None:
        logger.warning(NO_REAPER)
        return None
    reaper = Reaper(gateway.state.store, gateway.state.logs, rooms, gateway.state.live)
    return asyncio.ensure_future(reaping(reaper))


# In the background, because a gateway that waited for the SFU before opening would refuse every
# door while livekit came up beside it — and the deploy's health check knocks on those doors.
def _a_rebuild(gateway: FastAPI) -> asyncio.Task[None] | None:
    """The SIP side asked for again, started; None when this process holds no carrier tables."""
    state = gateway.state
    if state.carriers is None or state.trunks is None:
        return None

    async def rebuilt() -> None:
        found = await reconciled(
            state.orgs,
            state.carriers,
            state.routes,
            state.trunks,
            state.outbound_trunks,
            state.outbound,
        )
        logger.info(
            "the SIP side stands: %d org(s) with a trunk in, %d out, %d renumbered, %d refused",
            len(found.inbound),
            len(found.outbound),
            len(found.renumbered),
            len(found.refused),
        )

    return asyncio.ensure_future(rebuilt())


async def _cancelled(task: asyncio.Task[None]) -> None:
    """Stop the loop and wait for it: a pass half-written is a log half-ended."""
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


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


# The interactive schema lives under `/v1`, where every other door of this API lives, and not at
# FastAPI's default `/docs` — which is a SCREEN of the console (`/docs[/:base]`, the org's knowledge
# bases). A route wins over the catch-all that serves the page, so the default shadowed it: a link
# to `/docs` pasted to a colleague opened Swagger, and a reload of the screen they were on threw
# them out of the console (found against production, 2026-09-20). `/openapi.json` stays where every
# generator looks for it; nothing of the console answers to that name.
app = FastAPI(
    title="Pinecall gateway", lifespan=lifespan, docs_url="/v1/docs", redoc_url="/v1/redoc"
)


# Every door, in the order a reader meets them: api/routers.py is the list.
for door in DOORS:
    app.include_router(door)

# The two pages, LAST and alone: the console's route is the gateway's only catch-all, and it must
# come after every door above so that no /v1 path is ever answered with a page. api/pages.py.
app.include_router(pages.router)

# What every door above answers when the embedder refuses or the rows were written by another
# model: a status and the refusal's own sentence, in one table (api/refusals.py).
refusals_answered_by(app)

# The one caller of a door from another origin: Pinecall's own mobile app, by an allowlist and
# only under /v1 (api/origins.py). Every other origin is answered exactly as it was before.
app.add_middleware(AppOrigins)
