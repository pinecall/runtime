"""`pinecall-runtime orgs`: the tenants — list, add, rm, quota, provider-key, sso."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from functools import partial
from typing import Any, TextIO

from pinecall.cli.columns import as_columns
from pinecall.cli.operator import Operator, against_the_gateway
from pinecall.cli.orgs.sso import BREAK_GLASS, OPS_ORGS, sso
from pinecall.providers.catalog import vendors_with_a_key
from pinecall.types import QUOTAS, ROLES

# The one limit set with the quotas that is not one of them: nothing is refused over it.
BUDGET = "budget_eur"

PURPOSE: str = (
    "the tenants: list | add | invite | operator | move | rm | quota | provider-key | sso"
)
VERBS: tuple[str, ...] = (
    "list",
    "add",
    "invite",
    "operator",
    "move",
    "rm",
    "quota",
    "provider-key",
    "sso",
)

# What a quota reads as when nobody set it. The column is still a column.
NO_LIMIT = "—"

# What an org with no row of its own runs on, said in the one line `provider-key list` prints.
ON_THE_BOX = "this org runs every vendor on the keys of this box"

# The key is read from stdin and NEVER from a flag: argv is in `ps` on a shared box, and a key
# pasted as an argument is a key in the shell history of whoever typed it. Nothing prints it back.
READ_THE_KEY = "paste the {vendor} key and press enter: "
NO_KEY_ON_STDIN = "nothing came in on stdin: pipe the key, or paste it and press enter"

# Nobody of that org answers to the email: the sentence names both, because a typo in either is
# the same mistake and the person reading has to know which one to fix.
NO_SUCH_MEMBER = "no member of {org} answers to {email}"


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
    limiting.set_defaults(run=run_quota)

    _configure_provider_keys(verbs.add_parser("provider-key", help="an org's own vendor keys"))

    signing_in = verbs.add_parser("sso", help="which identity provider an org signs in with")
    signing_in.add_argument("org", metavar="<org>", help="by id or slug")
    signing_in.add_argument("--off", action="store_true", help=BREAK_GLASS)
    signing_in.set_defaults(run=run_sso)

    parser.set_defaults(run=partial(_print_the_verbs, parser))


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

    parser.set_defaults(run=partial(_print_the_verbs, parser))


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


def run_remove(arguments: argparse.Namespace) -> int:
    """One tenant less, once nothing of theirs is live."""
    return against_the_gateway(partial(remove_org, arguments.org))


def run_quota(arguments: argparse.Namespace) -> int:
    """The org's limits, replaced whole: a flag left out is no limit."""
    limits: dict[str, int | None] = {name: getattr(arguments, name) for name in (*QUOTAS, BUDGET)}
    return against_the_gateway(partial(set_quota, arguments.org, limits))


def run_move(arguments: argparse.Namespace) -> int:
    return against_the_gateway(partial(move_agent, arguments.agent, arguments.org))


def run_sso(arguments: argparse.Namespace) -> int:
    """Which provider the org signs in with, and — with --off — a password again beside it."""
    return against_the_gateway(partial(sso, arguments.org, arguments.off))


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


# The token is the person's way in and is shown exactly once, as a key is: the table keeps its
# sha256, it dies in a week, and no verb reads one back. The operator hands the LINK over — the
# console's own screen spends it for a password — never a key, and never a password of theirs.
TOKEN_PRINTED_ONCE = "send them this; it opens the console's password screen once, within a week"


async def invite(
    org: str, email: str, name: str, role: str, operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The person's row made, and the link that makes them a member printed once."""
    said = await operator.post(
        f"{OPS_ORGS}/{org}/members", {"email": email, "name": name, "role": role}
    )
    member = said["member"]
    print(f"{member['id']}  {member['email']}  {member['role']}  {member['status']}", file=out)
    if said.get("token") is None:
        print(f"  {ALREADY_A_PERSON}", file=out)
        return 0
    print(f"  {operator.base}/invitations/{said['token']}", file=out)
    print(f"  {TOKEN_PRINTED_ONCE}", file=out)
    return 0


# No link for somebody who already has a password on this box: they are seated at once, and
# the org appears in their console's org switch. A link would buy them a second password.
ALREADY_A_PERSON = "already a person on this box: seated, they sign in with the password they have"


# What an operator IS, said where somebody granting it will read it: their own key opens every
# /v1/ops door of this box, on top of their org's own. No role gives it and no org can grant it.
RUNS_THE_BOX = "their own key now opens this box's operator doors, as well as their org's"
RUNS_NO_MORE = "their key opens their org's doors and this box's no longer"


async def make_operator(
    org: str, email: str, running: bool, operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The member with that email, made an operator of this box or unmade. Their org stands."""
    people = await operator.get(f"{OPS_ORGS}/{org}/members")
    found = next((one for one in people["members"] if one["email"] == email.strip().lower()), None)
    if found is None:
        print(NO_SUCH_MEMBER.format(email=email, org=org), file=sys.stderr)
        return 1
    changed = await operator.put(
        f"{OPS_ORGS}/{org}/members/{found['id']}/operator", {"operator": running}
    )
    print(f"{changed['id']}  {changed['email']}  {changed['role']}", file=out)
    print(f"  {RUNS_THE_BOX if running else RUNS_NO_MORE}", file=out)
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
    org: str, limits: dict[str, int | None], operator: Operator, out: TextIO = sys.stdout
) -> int:
    """The limits as the door kept them, one per line, `—` for the ones left open."""
    kept = await operator.put(f"{OPS_ORGS}/{org}/quotas", limits)
    for name in (*QUOTAS, BUDGET):
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
