"""pinecall-runtime: the operator's verbs, and the ones the box's units run."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from types import FrameType
from typing import override
from urllib.parse import urlparse

import httpx
import uvicorn
from livekit import api

from pinecall.channels.routes import server_of
from pinecall.domain.errors import PinecallError
from pinecall.domain.settings import Settings, load
from pinecall.domain.types import DEFAULT_ORG, THE_FLEET, parse_env
from pinecall.gateway.app import announce_closing, app
from pinecall.postgres.migrate import apply_migrations, migrations_behind
from pinecall.postgres.pool import open_pool
from pinecall.tenancy import keys
from pinecall.tenancy.vault import vault_of
from pinecall.worker import main as worker

logger = logging.getLogger(__name__)

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
NOT_LOOPBACK = "PINECALL_GATEWAY_URL binds {host}: the gateway listens on loopback, behind Caddy"
A_FLEET_KEY = "the {env} fleet"


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the verb and run it; its exit is the process's."""
    said = _verbs().parse_args(argv)
    settings = load()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        sys.exit(said.run(settings, said))
    except PinecallError as refused:
        sys.stderr.write(f"pinecall-runtime: {refused}\n")
        sys.exit(1)


def _verbs() -> argparse.ArgumentParser:
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
    key_verbs = under.add_parser("keys", help="keys").add_subparsers(required=True)
    fleet = key_verbs.add_parser("fleet", help="mint a world's fleet key, printed once")
    fleet.add_argument("env", choices=("production", "sandbox"))
    fleet.set_defaults(run=fleet_key)
    under.add_parser("doctor", help="what this box lacks").set_defaults(run=doctor)
    return verbs


def gateway(settings: Settings, _said: argparse.Namespace) -> int:
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


# A stream never ends on its own: told nothing, a stop waits out uvicorn's grace and cuts it.
class Stopping(uvicorn.Server):
    """uvicorn's server, which tells the open streams when it is told to stop."""

    @override
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Tell the streams, then stop as uvicorn does."""
        announce_closing(app)
        super().handle_exit(sig, frame)


def start(settings: Settings, _said: argparse.Namespace) -> int:
    """A worker of the fleet, until told to stop or cordoned."""
    return asyncio.run(worker.run(settings))


def overflow(settings: Settings, _said: argparse.Namespace) -> int:
    """The fleet's overflow, until told to stop."""
    return asyncio.run(worker.overflow(settings))


def migrate_up(settings: Settings, _said: argparse.Namespace) -> int:
    """Apply the migrations the database lacks, and say which."""
    applied = asyncio.run(apply_migrations(settings.database_url))
    names = ", ".join(applied.applied) or "nothing: it was up to date"
    sys.stdout.write(f"{applied.database}/{applied.schema}: {names}\n")
    return 0


# Printed to stdout once, where the unit that mints it seals it: never to a terminal on a box.
def fleet_key(settings: Settings, said: argparse.Namespace) -> int:
    """Mint the fleet key of a world, in the box's own org."""
    env = parse_env(str(said.env))
    issued = keys.Issued(
        org=DEFAULT_ORG, env=env, scopes=frozenset({THE_FLEET}), label=A_FLEET_KEY.format(env=env)
    )
    sys.stdout.write(asyncio.run(_minted(settings, issued)))
    return 0


async def _minted(settings: Settings, issued: keys.Issued) -> str:
    pool = await open_pool(settings.database_url)
    try:
        _, secret = await keys.issue(pool, issued)
    finally:
        await pool.close()
    return secret


def doctor(settings: Settings, _said: argparse.Namespace) -> int:
    """Each thing the box needs, a line each; the exit is 1 when one is missing."""
    lines = asyncio.run(_examined(settings))
    for name, trouble in lines:
        said = "ok" if trouble is None else "NO"
        why = "" if trouble is None else f": {trouble}"
        sys.stdout.write(f"{said}  {name}{why}\n")
    return 0 if all(trouble is None for _, trouble in lines) else 1


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
