"""`pinecall-runtime orgs`: the tenants of this runtime — list, add, rm, quota, provider-key."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from typing import Any, TextIO

from pinecall.cli.columns import as_columns
from pinecall.cli.operator import Operator, against_the_gateway
from pinecall.types import QUOTAS, VENDORS

PURPOSE: str = "the tenants: list | add | rm | quota | provider-key"
VERBS: tuple[str, ...] = ("list", "add", "rm", "quota", "provider-key")

# The door every verb here knocks at, on PINECALL_OPS_KEY.
OPS_ORGS = "/v1/ops/orgs"

# What a quota reads as when nobody set it. The column is still a column.
NO_LIMIT = "—"

# What an org with no row of its own runs on, said in the one line `provider-key list` prints.
ON_THE_BOX = "this org runs every vendor on the keys of this box"

# The key is read from stdin and NEVER from a flag: argv is in `ps` on a shared box, and a key
# pasted as an argument is a key in the shell history of whoever typed it. Nothing prints it back.
READ_THE_KEY = "paste the {vendor} key and press enter: "
NO_KEY_ON_STDIN = "nothing came in on stdin: pipe the key, or paste it and press enter"


def configure(parser: argparse.ArgumentParser) -> None:
    """Four verbs, four parsers: each names its arguments, and `orgs` alone prints them."""
    # prog is the parser's own, not `f"{parser.prog} orgs"`: `orgs` is already in it, and the
    # doubled word showed up the moment a verb of this group grew verbs of its own.
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=parser.prog)

    listing = verbs.add_parser("list", help="every org, oldest first")
    listing.set_defaults(run=run_list)

    adding = verbs.add_parser("add", help="a new tenant, by the slug people will type")
    adding.add_argument("slug", metavar="<slug>", help="lowercase, digits and dashes")
    adding.add_argument("--name", default=None, help="what to call it (default: the slug)")
    adding.set_defaults(run=run_add)

    removing = verbs.add_parser("rm", help="forget an org; refused while it has keys or routes")
    removing.add_argument("org", metavar="<org>", help="by id or slug")
    removing.set_defaults(run=run_remove)

    limiting = verbs.add_parser("quota", help="set what an org may consume, the whole set at once")
    limiting.add_argument("org", metavar="<org>", help="by id or slug")
    for name in QUOTAS:
        flag = f"--{name.replace('_', '-')}"
        limiting.add_argument(flag, type=int, default=None, help=f"{name}; left out is no limit")
    limiting.set_defaults(run=run_quota)

    _configure_provider_keys(verbs.add_parser("provider-key", help="an org's own vendor keys"))

    parser.set_defaults(run=partial(_print_the_verbs, parser))


# A group of its own, because a provider key has three verbs of its own and hanging them off
# `orgs` directly would read as five unrelated words. `orgs provider-key set clinica elevenlabs`.
def _configure_provider_keys(parser: argparse.ArgumentParser) -> None:
    """Three verbs over one org's own vendor keys: set one, rm one, list which are set."""
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=parser.prog)

    setting = verbs.add_parser("set", help="the org's own key for a vendor, read from stdin")
    setting.add_argument("org", metavar="<org>", help="by id or slug")
    setting.add_argument("vendor", metavar="<vendor>", choices=VENDORS, help=" | ".join(VENDORS))
    setting.set_defaults(run=run_provider_key_set)

    removing = verbs.add_parser("rm", help="back to this box's own key for that vendor")
    removing.add_argument("org", metavar="<org>", help="by id or slug")
    removing.add_argument("vendor", metavar="<vendor>", choices=VENDORS, help=" | ".join(VENDORS))
    removing.set_defaults(run=run_provider_key_remove)

    listing = verbs.add_parser("list", help="which vendors this org brought a key for")
    listing.add_argument("org", metavar="<org>", help="by id or slug")
    listing.set_defaults(run=run_provider_key_list)

    parser.set_defaults(run=partial(_print_the_verbs, parser))


def run_list(arguments: argparse.Namespace) -> int:  # noqa: ARG001
    """Every org as one line."""
    return against_the_gateway(list_orgs)


def run_add(arguments: argparse.Namespace) -> int:
    """One tenant more. The id is minted on the gateway's side and printed here."""
    return against_the_gateway(partial(add_org, arguments.slug, arguments.name))


def run_remove(arguments: argparse.Namespace) -> int:
    """One tenant less, once nothing of theirs is live."""
    return against_the_gateway(partial(remove_org, arguments.org))


def run_quota(arguments: argparse.Namespace) -> int:
    """The org's limits, replaced whole: a flag left out is no limit."""
    limits = {name: getattr(arguments, name) for name in QUOTAS}
    return against_the_gateway(partial(set_quota, arguments.org, limits))


def run_provider_key_set(arguments: argparse.Namespace) -> int:
    """One vendor, one org, one key off stdin. The terminal never sees it again."""
    key = a_key_from(sys.stdin, arguments.vendor)
    if key is None:
        print(NO_KEY_ON_STDIN, file=sys.stderr)
        return 1
    return against_the_gateway(partial(set_provider_key, arguments.org, arguments.vendor, key))


def run_provider_key_remove(arguments: argparse.Namespace) -> int:
    """The org goes back to running that vendor on this box's own key."""
    return against_the_gateway(partial(remove_provider_key, arguments.org, arguments.vendor))


def run_provider_key_list(arguments: argparse.Namespace) -> int:
    """Which vendors the org brought a key for. Names, never values."""
    return against_the_gateway(partial(list_provider_keys, arguments.org))


# ── the verbs, as coroutines over an Operator a test can hand in ────────────────


async def list_orgs(operator: Operator, out: TextIO = sys.stdout) -> int:
    """Id, slug and name, one line each. The default org is always the first."""
    rows = await operator.get(OPS_ORGS)
    for line in as_columns([_row_of(org) for org in rows]):
        print(line, file=out)
    return 0


async def add_org(slug: str, name: str | None, operator: Operator, out: TextIO = sys.stdout) -> int:
    """A new org, and its minted id on the screen: that id is what every row of theirs names."""
    org = await operator.post(OPS_ORGS, {"slug": slug, "name": name})
    print(f"{org['id']}  {org['slug']}  {org['name']}", file=out)
    return 0


async def remove_org(org: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """The row goes. The door refuses while a live key or a route still names it."""
    await operator.delete(f"{OPS_ORGS}/{org}")
    print(f"org {org} removed", file=out)
    return 0


async def set_quota(
    org: str, limits: dict[str, int | None], operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The limits as the door kept them, one per line, `—` for the ones left open."""
    kept = await operator.put(f"{OPS_ORGS}/{org}/quotas", limits)
    for name in QUOTAS:
        limit = kept.get(name)
        print(f"  {name:<17} {NO_LIMIT if limit is None else limit}", file=out)
    return 0


async def set_provider_key(
    org: str, vendor: str, key: str, operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The org's own key for one vendor, from its next call on. Nothing of it is printed back."""
    await operator.put(f"{OPS_ORGS}/{org}/provider-keys/{vendor}", {"key": key})
    print(f"org {org} now runs {vendor} on its own key", file=out)
    return 0


async def remove_provider_key(
    org: str, vendor: str, operator: Operator, out: TextIO = sys.stdout
) -> int:
    """Forget it. The door refuses with 404 when the org had no key for that vendor."""
    await operator.delete(f"{OPS_ORGS}/{org}/provider-keys/{vendor}")
    print(f"org {org} is back on this box's {vendor} key", file=out)
    return 0


async def list_provider_keys(org: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """One line per vendor the org brought a key for, and one sentence when it brought none."""
    said = await operator.get(f"{OPS_ORGS}/{org}/provider-keys")
    vendors: list[str] = list(said["vendors"])
    for vendor in vendors:
        print(f"  {vendor}", file=out)
    if not vendors:
        print(f"  {ON_THE_BOX}", file=out)
    return 0


# stdin and not argv, and one line: a key is a secret, `ps` shows an argument to every user on the
# box, and a shell keeps it in its history. A pasted key with a trailing newline is the normal case.
def a_key_from(stdin: TextIO, vendor: str) -> str | None:
    """The one line the operator pasted, or None when nothing came in."""
    if stdin.isatty():
        print(READ_THE_KEY.format(vendor=vendor), end="", file=sys.stderr)
    return stdin.readline().strip() or None


def _row_of(org: dict[str, Any]) -> tuple[str, ...]:
    """One org as a person reads it."""
    return (str(org["id"]), str(org["slug"]), str(org["name"]))


def _print_the_verbs(parser: argparse.ArgumentParser, _arguments: Any) -> int:
    """`orgs` with no verb: say what there is, and exit as a help screen does."""
    parser.print_help()
    return 0
