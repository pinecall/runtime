"""pinecall-runtime: the operator's verbs, and the ones the box's units run."""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import override
from urllib.parse import urlparse

import httpx
import uvicorn
from livekit import api

from pinecall.cli import _operator, _sessions, _traceback
from pinecall.domain.errors import NotAvailable, PinecallError
from pinecall.gateway.app import announce_closing, app, embedder_of
from pinecall.postgres.migrate import apply_migrations, migration_files, migrations_behind
from pinecall.postgres.pool import open_pool
from pinecall.process.connections import opened, server_of, vault_of
from pinecall.process.settings import Settings, load
from pinecall.providers import catalog, prices
from pinecall.providers.build import installed
from pinecall.providers.catalog import Providers
from pinecall.retrieval import memory
from pinecall.tenancy import retention, vault
from pinecall.worker import main as worker

logger = logging.getLogger(__name__)


LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


NOT_LOOPBACK = "PINECALL_GATEWAY_URL binds {host}: the gateway listens on loopback, behind Caddy"


# A stream never ends on its own: told nothing, a stop waits out uvicorn's grace and cuts it.
class Stopping(uvicorn.Server):
    """uvicorn's server, which tells the open streams when it is told to stop."""

    @override
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Tell the streams, then stop as uvicorn does."""
        announce_closing(app)
        super().handle_exit(sig, frame)


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the verb and run it; its exit is the process's."""
    data = verbs().parse_args(argv)
    settings = load()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        sys.exit(data.run(settings, data))
    except PinecallError as refused:
        sys.stderr.write(f"pinecall-runtime: {refused}\n")
        sys.exit(1)


def gateway(settings: Settings, _args: argparse.Namespace) -> int:
    """Serve the gateway on the loopback address PINECALL_GATEWAY_URL names."""
    bound = urlparse(settings.gateway_url)
    host, port = bound.hostname or "127.0.0.1", bound.port or 8080
    if host not in LOOPBACK:
        raise PinecallError(NOT_LOOPBACK.format(host=host))
    # Caddy is the one proxy, on this machine: its X-Forwarded-* are believed from loopback only.
    config = uvicorn.Config(
        app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="127.0.0.1"
    )
    Stopping(config).run()
    return 0


def start(settings: Settings, _args: argparse.Namespace) -> int:
    """A worker of the fleet, until told to stop or cordoned."""
    return asyncio.run(worker.run(settings))


def overflow(settings: Settings, _args: argparse.Namespace) -> int:
    """The fleet's overflow, until told to stop."""
    return asyncio.run(worker.overflow(settings))


def migrate_up(settings: Settings, _args: argparse.Namespace) -> int:
    """Apply the migrations the database lacks, and say which."""
    applied = asyncio.run(apply_migrations(settings.database_url))
    names = ", ".join(applied.applied) or "nothing: it was up to date"
    sys.stdout.write(f"{applied.database}/{applied.schema}: {names}\n")
    return 0


def migrate_status(settings: Settings, _args: argparse.Namespace) -> int:
    """What the database has not run yet; the exit is 1 while it is behind."""
    behind = asyncio.run(_behind(settings))
    if not behind:
        sys.stdout.write("up to date\n")
        return 0
    sys.stdout.write(f"{len(behind)} behind: {', '.join(behind)}\n")
    return 1


def migrate_plan(_settings: Settings, _args: argparse.Namespace) -> int:
    """Every migration on the disk, in the order a run applies them; no database asked."""
    for path in migration_files():
        sys.stdout.write(f"{path.name}\n")
    return 0


def providers(settings: Settings, args: argparse.Namespace) -> int:
    """Every vendor this build runs, what it does, and whether the box holds its key."""
    with_a_key = asyncio.run(_box_vendors(settings))
    rows = sorted(installed().values(), key=lambda vendor: vendor.name)
    for vendor in rows:
        does = sorted(vendor.does)
        if args.does is not None and args.does not in does:
            continue
        state = "broken" if vendor.broken else ("ready" if vendor.name in with_a_key else "no key")
        sys.stdout.write(f"{vendor.name:16} {','.join(does):12} {state}\n")
    sys.stdout.write(f"{len(rows)} vendors\n")
    return 0


# Once: a box that has a row is edited from the console, and a second seed is refused.
def providers_seed(settings: Settings, args: argparse.Namespace) -> int:
    """Write the providers row a box starts from, off a JSON file."""
    seeded = Providers.model_validate_json(Path(args.file).read_text(encoding="utf-8"))
    asyncio.run(_seeded(settings, seeded))
    sys.stdout.write("the providers row is written: the console edits it from now on\n")
    return 0


# Shown by default: a file written by mistake replaces what the console set.
def providers_prices(settings: Settings, args: argparse.Namespace) -> int:
    """What a prices file changes in the box's rates, and with --apply, the rates written."""
    written = prices.rates_from_csv(Path(args.file).read_text(encoding="utf-8"))
    box = asyncio.run(_box_providers(settings))
    change = prices.rates_changed(box.rates, written)
    if args.apply:
        rates = {**box.rates, **written}
        asyncio.run(_configured(settings, box.model_copy(update={"rates": rates})))
    for mark, models in (("+", change.added), ("~", change.changed)):
        sys.stdout.writelines(f"{mark} {model}\n" for model in models)
    sys.stdout.write(
        f"{len(change.added)} new, {len(change.changed)} changed, {len(change.unchanged)} the "
        f"same, {len(change.only_on_the_box)} only on the box and kept\n"
    )
    done = "written" if args.apply else "nothing written: --apply writes them"
    sys.stdout.write(f"{done}\n")
    return 0


def memory_reembed(settings: Settings, _args: argparse.Namespace) -> int:
    """Every fact another model embedded, embedded again by the box's; how many there were."""
    count = asyncio.run(_reembedded(settings))
    sys.stdout.write(f"{count} facts re-embedded\n")
    return 0


def retention_due(settings: Settings, _args: argparse.Namespace) -> int:
    """The sealed calls the next run would erase, one line each, and how many."""
    calls = asyncio.run(_due(settings))
    for call in calls:
        sys.stdout.write(f"{call.call}  {call.scope.org} {call.scope.env}\n")
    sys.stdout.write(f"{len(calls)} calls past their org's days\n")
    return 0


def retention_run(settings: Settings, _args: argparse.Namespace) -> int:
    """Erase every sealed call past its org's days, forget old records and dials; how many."""
    erased, records, dials = asyncio.run(_purged(settings))
    sys.stdout.write(f"{len(erased)} calls erased past their org's days\n")
    sys.stdout.write(f"{records} call records and {dials} dials forgotten past 24 months\n")
    return 0


def doctor(settings: Settings, _args: argparse.Namespace) -> int:
    """Each thing the box needs, a line each; the exit is 1 when one is missing."""
    lines = asyncio.run(_examined(settings))
    for name, trouble in lines:
        text = "ok" if trouble is None else "NO"
        why = "" if trouble is None else f": {trouble}"
        sys.stdout.write(f"{text}  {name}{why}\n")
    return 0 if all(trouble is None for _, trouble in lines) else 1


def verbs() -> argparse.ArgumentParser:
    """The parser of every group and verb, each bound to the function that runs it."""
    verbs = argparse.ArgumentParser(prog="pinecall-runtime")
    under = verbs.add_subparsers(required=True)
    under.add_parser("gateway", help="the gateway, both worlds").set_defaults(run=gateway)
    worker_verbs = under.add_parser("worker", help="a worker of the fleet PINECALL_FLEET names")
    of_worker = worker_verbs.add_subparsers(required=True)
    of_worker.add_parser("start", help="answer the fleet's calls").set_defaults(run=start)
    of_worker.add_parser("overflow", help="answer when the fleet is full").set_defaults(
        run=overflow
    )
    migrate = under.add_parser("migrate", help="the schema").add_subparsers(required=True)
    migrate.add_parser("up", help="apply what the database lacks").set_defaults(run=migrate_up)
    migrate.add_parser("status", help="what the database lacks").set_defaults(run=migrate_status)
    migrate.add_parser("plan", help="every migration, off the disk").set_defaults(run=migrate_plan)
    _operator.keys_group(under.add_parser("keys", help="keys"))
    under.add_parser("doctor", help="what this box lacks").set_defaults(run=doctor)
    vendors = under.add_parser(
        "providers", help="the vendors this build runs, and the row"
    ).add_subparsers(required=True)
    listing = vendors.add_parser("list", help="every vendor this build runs")
    listing.add_argument("--does", choices=("llm", "stt", "tts"), default=None)
    listing.set_defaults(run=providers)
    seed = vendors.add_parser("seed", help="the providers row a box starts from, once")
    seed.add_argument("file", help="a JSON file of the row")
    seed.set_defaults(run=providers_seed)
    pricing = vendors.add_parser("prices", help="the rates a prices file sets, shown or written")
    pricing.add_argument("file", help="a CSV: vendor,model,unit,usd,as_of,source")
    pricing.add_argument("--apply", action="store_true", help="write them into the box's row")
    pricing.set_defaults(run=providers_prices)
    memory_verbs = under.add_parser("memory", help="contact memory").add_subparsers(required=True)
    memory_verbs.add_parser("reembed", help="every fact under the box's embedder").set_defaults(
        run=memory_reembed
    )
    kept = under.add_parser("retention", help="the calls past their org's days")
    kept_verbs = kept.add_subparsers(required=True)
    kept_verbs.add_parser("due", help="what the next run would erase").set_defaults(
        run=retention_due
    )
    kept_verbs.add_parser("run", help="erase them; the box's timer runs it nightly").set_defaults(
        run=retention_run
    )
    _sessions.sessions_group(under.add_parser("sessions", help="the log, off Postgres"))
    _traceback.traceback_verb(
        under.add_parser("traceback", help="a number's calls and dials, for a carrier")
    )
    _operator.init_group(under.add_parser("init", help="the first org and person, on a fresh box"))
    _operator.orgs_group(under.add_parser("orgs", help="the tenants"))
    _operator.routes_group(under.add_parser("routes", help="which agent answers a number"))
    _operator.fleet_group(under.add_parser("fleet", help="the workers heard from"))
    return verbs


async def _behind(settings: Settings) -> tuple[str, ...]:
    pool = await open_pool(settings.database_url)
    try:
        return await migrations_behind(pool)
    finally:
        await pool.close()


async def _box_vendors(settings: Settings) -> frozenset[str]:
    pool = await open_pool(settings.database_url)
    try:
        return frozenset(await vault.box_credentials(pool, vault_of(settings.vault_key)))
    finally:
        await pool.close()


async def _seeded(settings: Settings, seeded: Providers) -> None:
    pool = await open_pool(settings.database_url)
    try:
        await catalog.seed(pool, seeded)
    finally:
        await pool.close()


async def _box_providers(settings: Settings) -> Providers:
    pool = await open_pool(settings.database_url)
    try:
        return await catalog.providers(pool)
    finally:
        await pool.close()


async def _configured(settings: Settings, edited: Providers) -> None:
    pool = await open_pool(settings.database_url)
    try:
        await catalog.configure(pool, edited)
    finally:
        await pool.close()


async def _reembedded(settings: Settings) -> int:
    async with opened(settings) as connections:
        embedder = await embedder_of(connections)
        if embedder is None:
            raise NotAvailable("this box embeds nothing: no embedding in its providers row")
        return await memory.reembed(connections.pool, embedder)


# The database alone: no vault, no LiveKit, so the nightly unit is handed nothing else.
async def _due(settings: Settings) -> list[retention.Due]:
    pool = await open_pool(settings.database_url)
    try:
        return await retention.due(pool, time.time())
    finally:
        await pool.close()


async def _purged(settings: Settings) -> tuple[list[str], int, int]:
    pool = await open_pool(settings.database_url)
    now = time.time()
    try:
        erased = await retention.purge(pool, Path(settings.recordings_root), now)
        records = await retention.forget_records(pool, now)
        return erased, records, await retention.forget_dials(pool, now)
    finally:
        await pool.close()


async def _examined(settings: Settings) -> list[tuple[str, str | None]]:
    return [
        ("vault", _vault(settings)),
        ("database", await _database(settings)),
        ("livekit", await _livekit(settings)),
        ("gateway", await _gateway(settings)),
    ]


def _vault(settings: Settings) -> str | None:
    try:
        vault_of(settings.vault_key)
    except PinecallError as refused:
        return str(refused)
    return None


async def _database(settings: Settings) -> str | None:
    try:
        pool = await open_pool(settings.database_url)
    except PinecallError as refused:
        return str(refused)
    try:
        behind = await migrations_behind(pool)
    finally:
        await pool.close()
    return f"{len(behind)} migrations behind: {', '.join(behind)}" if behind else None


async def _livekit(settings: Settings) -> str | None:
    try:
        server = server_of(settings)
    except PinecallError as refused:
        return str(refused)
    try:
        await server.room.list_rooms(api.ListRoomsRequest())
    except (api.TwirpError, OSError) as refused:
        return str(refused)
    finally:
        await server.aclose()
    return None


async def _gateway(settings: Settings) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            answer = await http.get(f"{settings.gateway_url}/v1/docs")
    except httpx.HTTPError as unreachable:
        return str(unreachable)
    return (
        None if answer.status_code < httpx.codes.BAD_REQUEST else f"answered {answer.status_code}"
    )
