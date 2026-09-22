"""`pinecall-runtime routes`: a number is a route to an agent — list, add, rm, seed."""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path
from typing import Any, TextIO, cast

from pinecall.cli.columns import as_columns
from pinecall.cli.operator import Operator, OperatorRefused, against_the_gateway, with_an_org
from pinecall.types import ENVS, PRODUCTION

PURPOSE: str = "numbers and channels: list | add | rm | seed"
VERBS: tuple[str, ...] = ("list", "add", "rm", "seed")

# The door every verb here knocks at. `--org` and its default come from cli/operator.py, which
# is where every /v1/ops verb takes the same flag.
OPS_ROUTES = "/v1/ops/routes"

# A number answers the phone unless the operator says otherwise; WhatsApp is the same number on
# another channel, and the widget is not a number at all.
DEFAULT_CHANNEL = "phone"

# The routes a fresh box comes up with, kept beside the compose files rather than in the database:
# `routes seed` is how a clone of this repo answers a call without anybody typing four verbs.
SEED_FILE = "infra/seed/routes.json"


def configure(parser: argparse.ArgumentParser) -> None:
    """Four verbs, four parsers: each names its arguments, and `routes` alone prints them."""
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=parser.prog)

    listing = verbs.add_parser("list", help="every door the org answers")
    with_an_org(listing)
    _in_a_world(listing)
    listing.set_defaults(run=run_list)

    adding = verbs.add_parser("add", help="a number answers for this agent, from the next call")
    adding.add_argument("number", metavar="<number>", help="in E.164 form, +598…")
    adding.add_argument("agent", metavar="<agent>", help="the agent's slug")
    adding.add_argument("--channel", default=DEFAULT_CHANNEL, help="phone (default) or whatsapp")
    with_an_org(adding)
    _in_a_world(adding)
    adding.set_defaults(run=run_add)

    removing = verbs.add_parser("rm", help="the org stops answering this number")
    removing.add_argument("number", metavar="<number>", help="in E.164 form, +598…")
    with_an_org(removing)
    removing.set_defaults(run=run_remove)

    seeding = verbs.add_parser("seed", help=f"apply a file of routes (default {SEED_FILE})")
    seeding.add_argument("--file", default=SEED_FILE, help="a JSON array of routes")
    seeding.set_defaults(run=run_seed)

    parser.set_defaults(run=partial(_print_the_verbs, parser))


def run_list(arguments: argparse.Namespace) -> int:
    """Every door of one org in one world, and which table put it there."""
    return against_the_gateway(partial(list_routes, arguments.org, env=arguments.env))


def run_add(arguments: argparse.Namespace) -> int:
    """One number to one agent, live from the next call: nothing is restarted."""
    return against_the_gateway(
        partial(
            add_route,
            arguments.org,
            arguments.number,
            arguments.agent,
            arguments.channel,
            env=arguments.env,
        )
    )


def run_remove(arguments: argparse.Namespace) -> int:
    """One number the org stops answering."""
    return against_the_gateway(partial(remove_route, arguments.org, arguments.number))


def run_seed(arguments: argparse.Namespace) -> int:
    """A file of routes, applied in order: what a fresh box comes up answering."""
    return against_the_gateway(partial(seed_routes, Path(arguments.file)))


# ── the verbs, as coroutines over an Operator a test can hand in ────────────────


async def list_routes(
    org: str, operator: Operator, out: TextIO = sys.stdout, *, env: str = PRODUCTION
) -> int:
    """Every door as one line. Nothing to list is a sentence, not an empty screen."""
    answering = await operator.get(OPS_ROUTES, org=org, env=env)
    if not answering:
        print(f"no routes in org {org} in {env}", file=out)
        return 0
    for line in as_columns([_row_of(door) for door in answering]):
        print(line, file=out)
    return 0


async def add_route(
    org: str,
    number: str,
    agent: str,
    channel: str,
    operator: Operator,
    out: TextIO = sys.stdout,
    *,
    env: str = PRODUCTION,
) -> int:
    """One number to one agent. A number already answering somewhere moves: this is an upsert."""
    said = {"org": org, "number": number, "agent": agent, "channel": channel, "env": env}
    await operator.post(OPS_ROUTES, said)
    print(f"{number} {channel} → {agent} in org {org} · {env}", file=out)
    return 0


async def remove_route(org: str, number: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """The row goes; whatever a running app declares for that number answers again."""
    await operator.delete(f"{OPS_ROUTES}/{number}", org=org)
    print(f"{number} removed from org {org}", file=out)
    return 0


async def seed_routes(path: Path, operator: Operator, out: TextIO = sys.stdout) -> int:
    """Every route in the file, applied in order. A file that is not there is an error, not zero."""
    if not path.exists():
        print(f"no such file: {path}", file=out)
        return 1
    for route in _read_the_file(path):
        await add_route(
            route["org"],
            route["number"],
            route["agent"],
            route.get("channel", DEFAULT_CHANNEL),
            operator,
            out,
            env=route.get("env", PRODUCTION),
        )
    return 0


def _read_the_file(path: Path) -> list[dict[str, Any]]:
    """The seed file: a JSON array of routes, each the body `routes add` would have sent."""
    read: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(read, list):
        raise OperatorRefused(f"{path}: a seed file is a JSON array of routes")
    return cast("list[dict[str, Any]]", read)


def _row_of(route: dict[str, Any]) -> tuple[str, ...]:
    """One door as a person reads it: the number, the channel, and who answers there."""
    return (str(route.get("number") or "—"), str(route["channel"]), str(route["agent"]))


# ── the plumbing every verb shares ──────────────────────────────────────────────


# A number somebody bought rings the deployed agent, so production is the default; typing one
# into the sandbox is the deliberate act, and the flag is the same word `keys issue` takes.
def _in_a_world(parser: argparse.ArgumentParser) -> None:
    """Which world's doors this verb speaks about."""
    parser.add_argument(
        "--env",
        default=PRODUCTION,
        choices=sorted(ENVS),
        help=f"which world the number answers in (default {PRODUCTION})",
    )


def _print_the_verbs(parser: argparse.ArgumentParser, _arguments: Any) -> int:
    """`routes` with no verb: say what there is, and exit as a help screen does."""
    parser.print_help()
    return 0
