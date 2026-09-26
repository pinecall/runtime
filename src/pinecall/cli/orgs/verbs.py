"""`pinecall-runtime orgs`: the tenants — list, add, rm, quota, provider-key, sso, their people."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from functools import partial
from typing import Any, TextIO

from pinecall.cli.columns import aligned_columns
from pinecall.cli.help import help_only
from pinecall.cli.operator import OPS_ORGS, Operator, against_the_gateway
from pinecall.cli.orgs.members import invite, make_operator, remove_member
from pinecall.cli.orgs.provider_keys import (
    list_provider_keys,
    read_key_from,
    remove_provider_key,
    set_provider_key,
)
from pinecall.cli.orgs.sso import BREAK_GLASS, sso
from pinecall.providers.catalog import vendors_with_a_key
from pinecall.types import QUOTAS, ROLES

# The one limit set with the quotas that is not one of them: nothing is refused over it.
BUDGET = "budget_eur"

PURPOSE: str = (
    "the tenants: list | add | invite | operator | remove-member | move | rm | quota | "
    "dialling | provider-key | sso"
)
VERBS: tuple[str, ...] = (
    "list",
    "add",
    "invite",
    "operator",
    "remove-member",
    "move",
    "rm",
    "quota",
    "dialling",
    "provider-key",
    "sso",
)

# The lending, beside the quotas and not one of them: nothing is counted against it.
LENDS = "lends"
LENDS_NOTHING = "none"
LENDS_ALL = "every key of the box"

# What a quota reads as when nobody set it. The column is still a column.
NO_LIMIT = "—"

# The four an operator turns per org, in the order the door and the table declare them.
DIAL_GUARDS = ("dial_anywhere", "per_minute", "per_day", "max_duration_s")

# What an org with no row of its own runs on, said in the one line `provider-key list` prints.

# The key is read from stdin and NEVER from a flag: argv is in `ps` on a shared box, and a key
# pasted as an argument is a key in the shell history of whoever typed it. Nothing prints it back.
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

    inviting = verbs.add_parser("invite", help="an org's first admin, or one more person")
    inviting.add_argument("org", metavar="<org>", help="by id or slug")
    inviting.add_argument("email", metavar="<email>", help="where the person will be reached")
    inviting.add_argument("--name", required=True, help="their name, as a seat will say it")
    inviting.add_argument(
        "--role",
        default="admin",
        choices=sorted(ROLES),
        help="what their keys will open (default admin: the first person owns the org)",
    )
    inviting.set_defaults(run=run_invite)

    moving = verbs.add_parser("move", help=MOVE_HELP)
    moving.add_argument("agent", metavar="<agent>", help="the slug, as the class declares it")
    moving.add_argument("org", metavar="<org>", help="where it lands, by id or slug")
    moving.set_defaults(run=run_move)

    running = verbs.add_parser("operator", help="a person of an org runs this box, or stops")
    running.add_argument("org", metavar="<org>", help="by id or slug")
    running.add_argument("email", metavar="<email>", help="a member of that org")
    running.add_argument(
        "--revoke", action="store_true", help="take it back; their org's doors are untouched"
    )
    running.set_defaults(run=run_operator)

    unseating = verbs.add_parser("remove-member", help="a person out of an org, for good")
    unseating.add_argument("org", metavar="<org>", help="by id or slug")
    unseating.add_argument("email", metavar="<email>", help="a member of that org")
    unseating.set_defaults(run=run_remove_member)

    removing = verbs.add_parser("rm", help="forget an org; refused while it has keys or routes")
    removing.add_argument("org", metavar="<org>", help="by id or slug")
    removing.set_defaults(run=run_remove)

    limiting = verbs.add_parser("quota", help="set what an org may consume, the whole set at once")
    limiting.add_argument("org", metavar="<org>", help="by id or slug")
    for name in QUOTAS:
        flag = f"--{name.replace('_', '-')}"
        limiting.add_argument(flag, type=int, default=None, help=f"{name}; left out is no limit")
    limiting.add_argument(
        f"--{BUDGET.replace('_', '-')}",
        type=int,
        default=None,
        help="euros a calendar month, shown beside what was spent and never refused; out is none",
    )
    limiting.add_argument(
        f"--{LENDS}",
        default=None,
        metavar="<vendor[/model],…>",
        help="which of the box's keys it may run on: comma separated, `none` for none; out is all",
    )
    limiting.set_defaults(run=run_quota)

    dialling = verbs.add_parser("dialling", help="set what an org may dial out, the whole set")
    dialling.add_argument("org", metavar="<org>", help="by id or slug")
    # The one guard that turns a call-back box into one that can dial strangers. Off unless said.
    dialling.add_argument(
        "--dial-anywhere", action=argparse.BooleanOptionalAction, default=None,
        help="let it dial a number that never called it",
    )  # fmt: skip
    dialling.add_argument("--per-minute", type=int, default=None, help="dials a minute; out is 6")
    dialling.add_argument("--per-day", type=int, default=None, help="dials a day; out is 200")
    dialling.add_argument("--max-duration-s", type=int, default=None, help="seconds; out is 600")
    dialling.set_defaults(run=run_dialling)

    _configure_provider_keys(verbs.add_parser("provider-key", help="an org's own vendor keys"))

    signing_in = verbs.add_parser("sso", help="which identity provider an org signs in with")
    signing_in.add_argument("org", metavar="<org>", help="by id or slug")
    signing_in.add_argument("--off", action="store_true", help=BREAK_GLASS)
    signing_in.set_defaults(run=run_sso)

    parser.set_defaults(run=help_only(parser))


# Forty-odd names is not a help line, so the sentence names the door that prints them all with
# what each one does. argparse still refuses a word that is not one of them, and lists them then.
A_VENDOR = "any vendor this build runs — `pinecall-runtime providers` lists every one"


# The one verb that undoes what a first install gets wrong. A slug belongs to the org that first
# registered it, for as long as its log exists — and a box issues its own worker and operator keys
# into `default`, so the first agent anybody runs on a fresh box lands there and stays. Every call
# it has taken moves with it; a slug nobody has ever run is a 404, and one somebody is holding
# right now is refused until they stop it.
MOVE_HELP = "an agent, every call it has taken and its numbers, into another org"


# A group of its own, because a provider key has three verbs of its own and hanging them off
# `orgs` directly would read as five unrelated words. `orgs provider-key set clinica elevenlabs`.
def _configure_provider_keys(parser: argparse.ArgumentParser) -> None:
    """Three verbs over one org's own vendor keys: set one, rm one, list which are set."""
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=parser.prog)

    setting = verbs.add_parser("set", help="the org's own key for a vendor, read from stdin")
    setting.add_argument("org", metavar="<org>", help="by id or slug")
    setting.add_argument("vendor", metavar="<vendor>", choices=vendors_with_a_key(), help=A_VENDOR)
    setting.set_defaults(run=run_provider_key_set)

    removing = verbs.add_parser("rm", help="back to this box's own key for that vendor")
    removing.add_argument("org", metavar="<org>", help="by id or slug")
    removing.add_argument("vendor", metavar="<vendor>", choices=vendors_with_a_key(), help=A_VENDOR)
    removing.set_defaults(run=run_provider_key_remove)

    listing = verbs.add_parser("list", help="which vendors this org brought a key for")
    listing.add_argument("org", metavar="<org>", help="by id or slug")
    listing.set_defaults(run=run_provider_key_list)

    parser.set_defaults(run=help_only(parser))


def run_list(arguments: argparse.Namespace) -> int:  # noqa: ARG001
    """Every org as one line."""
    return against_the_gateway(list_orgs)


def run_add(arguments: argparse.Namespace) -> int:
    """One tenant more. The id is minted on the gateway's side and printed here."""
    return against_the_gateway(partial(add_org, arguments.slug, arguments.name))


def run_invite(arguments: argparse.Namespace) -> int:
    """One person into one org, the token on this terminal and nowhere else."""
    return against_the_gateway(
        partial(invite, arguments.org, arguments.email, arguments.name, arguments.role)
    )


def run_operator(arguments: argparse.Namespace) -> int:
    """One person of one org made — or unmade — an operator of this box."""
    return against_the_gateway(
        partial(make_operator, arguments.org, arguments.email, not arguments.revoke)
    )


def run_remove_member(arguments: argparse.Namespace) -> int:
    """One person out of one org for good: their keys stopped, their row and links gone."""
    return against_the_gateway(partial(remove_member, arguments.org, arguments.email))


def run_remove(arguments: argparse.Namespace) -> int:
    """One tenant less, once nothing of theirs is live."""
    return against_the_gateway(partial(remove_org, arguments.org))


def run_quota(arguments: argparse.Namespace) -> int:
    """The org's limits, replaced whole: a flag left out is no limit."""
    limits: dict[str, Any] = {name: getattr(arguments, name) for name in (*QUOTAS, BUDGET)}
    limits[LENDS] = parse_lending_flag(arguments.lends)
    return against_the_gateway(partial(set_quota, arguments.org, limits))


# `--lends deepgram,anthropic/claude-haiku-4-5` is those entries; `--lends none` is the empty set,
# the org running only on its own keys; the flag left out is every one, as every limit left out is
# no limit. The door checks each entry against the catalogue, so a typo is its sentence.
def parse_lending_flag(typed: str | None) -> list[str] | None:
    """The lending as the door takes it, from what the operator typed."""
    if typed is None:
        return None
    if typed.strip().lower() == LENDS_NOTHING:
        return []
    return [entry.strip() for entry in typed.split(",") if entry.strip()]


# Replaced whole, as the quotas are — but a guard left out goes back to the code's own default and
# not to no limit: there is no such thing as an org that may dial with no fence at all.
def run_dialling(arguments: argparse.Namespace) -> int:
    """The org's outbound guards, replaced whole."""
    said: dict[str, Any] = {name: getattr(arguments, name) for name in DIAL_GUARDS}
    return against_the_gateway(partial(set_dialling, arguments.org, said))


def run_move(arguments: argparse.Namespace) -> int:
    return against_the_gateway(partial(move_agent, arguments.agent, arguments.org))


def run_sso(arguments: argparse.Namespace) -> int:
    """Which provider the org signs in with, and — with --off — a password again beside it."""
    return against_the_gateway(partial(sso, arguments.org, arguments.off))


def run_provider_key_set(arguments: argparse.Namespace) -> int:
    """One vendor, one org, one key off stdin. The terminal never sees it again."""
    key = read_key_from(sys.stdin, arguments.vendor)
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
    for line in aligned_columns([_row_of(org) for org in rows]):
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


async def move_agent(agent: str, org: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """The agent, every call of it and its doors, into another org. Says what went with it."""
    said = await operator.put(f"{OPS_ORGS}/{org}/agents", {"agent": agent})
    numbers = _said_numbers(said, "numbers")
    moved = f"{said['agent']} → org {said['org']} · {said['logs']} logs"
    print(f"{moved} · {len(numbers)} numbers" if numbers else moved, file=out)
    # A number the other org already answers at is left where it was: which of two rows takes a
    # call is not this verb's to decide, and silence about it is a door somebody thinks moved.
    for number in _said_numbers(said, "stayed"):
        print(f"  {number} stayed: org {said['org']} already answers at it", file=out)
    return 0


def _said_numbers(said: Mapping[str, Any], field: str) -> tuple[str, ...]:
    """A list of numbers off an answer, as strings. A gateway too old for the field says none."""
    found: list[Any] | None = said.get(field)
    return () if found is None else tuple(str(one) for one in found)


async def set_quota(
    org: str, limits: dict[str, Any], operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The limits as the door kept them, one per line, `—` for the ones left open."""
    kept = await operator.put(f"{OPS_ORGS}/{org}/quotas", limits)
    for name in (*QUOTAS, BUDGET):
        limit = kept.get(name)
        print(f"  {name:<17} {NO_LIMIT if limit is None else limit}", file=out)
    lent = kept.get(LENDS)
    said = LENDS_ALL if lent is None else (", ".join(lent) or LENDS_NOTHING)
    print(f"  {LENDS:<17} {said}", file=out)
    return 0


async def set_dialling(
    org: str, said: dict[str, Any], operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The guards as the door kept them, one per line: what the next dial of this org passes."""
    kept = await operator.put(f"{OPS_ORGS}/{org}/dialling", said)
    for name in DIAL_GUARDS:
        guard = kept.get(name)
        print(f"  {name:<17} {guard}", file=out)
    return 0


def _row_of(org: dict[str, Any]) -> tuple[str, ...]:
    """One org as a person reads it."""
    return (str(org["id"]), str(org["slug"]), str(org["name"]))
