"""The operator's verbs over the box's own doors: init, orgs, keys, routes and the fleet."""

import argparse
import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from pinecall.domain.errors import DeclarationRefused, GatewayRefused, PinecallError
from pinecall.domain.names import Json, JsonObject, parse_env
from pinecall.domain.org import DEFAULT_ORG, QUOTAS
from pinecall.domain.person import ROLES, THE_FLEET, THE_RUNNER
from pinecall.fleet import hub, roster
from pinecall.fleet.hub import Cloud, Line
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from pinecall.tenancy import keys as key_table
from pinecall.wire.rest.fleet import WorkerStatus

type Verb = Callable[[httpx.Client, argparse.Namespace], int]


# The ops doors read tables; five seconds means the database is down.
TIMEOUT_S = 5.0


ORGS = "/v1/ops/orgs"


NO_OPS_KEY = (
    "PINECALL_OPS_KEY is not set: the operator API is closed, and these verbs speak nothing else"
)


ALREADY = "is taken"


THE_FIRST_ROLE = "admin"


WHAT_TO_DO_NEXT = """
  Open the link above to set a password. Then, in the directory of an agent:

    pinecall login {url}
    pinecall link
    pinecall start
"""


NO_BODY = 204


A_FLEET_KEY = "the {env} fleet"


A_RUNNER_KEY = "the {env} runner"

NO_GROWTH = "--grow-at-most is at least 1 and --target a share of the seats over 0"


def init_group(first: argparse.ArgumentParser) -> None:
    """`init`: the first org and the first person, on a fresh box."""
    first.add_argument("--org", default=DEFAULT_ORG, metavar="<slug>")
    first.add_argument("--email", required=True)
    first.add_argument("--person", required=True, metavar="<name>")
    first.add_argument("--name", default=None, help="what to call the org (default: the slug)")
    first.add_argument("--role", default=THE_FIRST_ROLE, choices=sorted(ROLES))
    first.set_defaults(run=_knocking(init))


def orgs_group(group: argparse.ArgumentParser) -> None:
    """`orgs`: the tenants, each verb bound to the function that runs it."""
    under = group.add_subparsers(required=True)
    under.add_parser("list", help="every org").set_defaults(run=_knocking(orgs_list))
    add = under.add_parser("add", help="a new org")
    add.add_argument("slug")
    add.add_argument("--name", default=None)
    add.set_defaults(run=_knocking(orgs_add))
    invite = under.add_parser("invite", help="a person into an org, the link once")
    invite.add_argument("org")
    invite.add_argument("email")
    invite.add_argument("--name", required=True)
    invite.add_argument("--role", default=THE_FIRST_ROLE, choices=sorted(ROLES))
    invite.set_defaults(run=_knocking(orgs_invite))
    runs = under.add_parser("operator", help="a member made an operator of this box")
    runs.add_argument("org")
    runs.add_argument("email")
    runs.add_argument("--revoke", action="store_true")
    runs.set_defaults(run=_knocking(orgs_operator))
    out = under.add_parser("remove-member", help="a person out of the org for good")
    out.add_argument("org")
    out.add_argument("email")
    out.set_defaults(run=_knocking(orgs_remove_member))
    move = under.add_parser("move", help="an agent's logs and numbers into an org")
    move.add_argument("agent")
    move.add_argument("org")
    move.set_defaults(run=_knocking(orgs_move))
    gone = under.add_parser("rm", help="an org forgotten")
    gone.add_argument("org")
    gone.set_defaults(run=_knocking(orgs_rm))
    _limits_verbs(
        under.add_parser("quota", help="the org's limits in one world, replaced whole"),
        under.add_parser("dialling", help="the org's dial guards, replaced whole"),
        under.add_parser("sso", help="the org's identity provider; --off is the break-glass"),
        under.add_parser("provider-key", help="the org's own vendor keys"),
    )


# The fleet key is minted on the database, never over a door: the box's own units get theirs
# before any gateway answers.
def keys_group(group: argparse.ArgumentParser) -> None:
    """`keys`: an org's keys minted, listed and revoked, and a world's fleet key."""
    keys = group.add_subparsers(required=True)
    issue = keys.add_parser("issue", help="a key of an org, printed once")
    issue.add_argument("--org", default=DEFAULT_ORG)
    issue.add_argument("--env", required=True, choices=("production", "sandbox"))
    issue.add_argument("--label", default=None)
    issue.add_argument("--scope", action="append", default=None)
    issue.add_argument("--subject", default=None)
    issue.add_argument("--name", default=None)
    issue.set_defaults(run=_knocking(keys_issue))
    listing = keys.add_parser("list", help="the org's keys by fingerprint")
    listing.add_argument("--org", default=DEFAULT_ORG)
    listing.set_defaults(run=_knocking(keys_list))
    revoke = keys.add_parser("revoke", help="one key stopped")
    revoke.add_argument("fingerprint")
    revoke.set_defaults(run=_knocking(keys_revoke))
    fleet = keys.add_parser("fleet", help="mint a world's fleet key, printed once")
    fleet.add_argument("env", choices=("production", "sandbox"))
    fleet.set_defaults(run=fleet_key)
    runner = keys.add_parser("runner", help="mint a world's runner key, printed once")
    runner.add_argument("env", choices=("production", "sandbox"))
    runner.set_defaults(run=runner_key)


def routes_group(group: argparse.ArgumentParser) -> None:
    """`routes`: which agent answers a number, in which world."""
    under = group.add_subparsers(required=True)
    listing = under.add_parser("list")
    listing.add_argument("--org", default=DEFAULT_ORG)
    listing.add_argument("--env", default="production", choices=("production", "sandbox"))
    listing.set_defaults(run=_knocking(routes_list))
    add = under.add_parser("add")
    add.add_argument("number")
    add.add_argument("agent")
    add.add_argument("--channel", default="phone", choices=("phone", "whatsapp"))
    add.add_argument("--org", default=DEFAULT_ORG)
    add.add_argument("--env", default="production", choices=("production", "sandbox"))
    add.set_defaults(run=_knocking(routes_add))
    gone = under.add_parser("rm")
    gone.add_argument("number")
    gone.add_argument("--org", default=DEFAULT_ORG)
    gone.set_defaults(run=_knocking(routes_rm))
    seed = under.add_parser("seed", help="a file of routes, each added")
    seed.add_argument("--file", default="infra/seed/routes.json")
    seed.set_defaults(run=_knocking(routes_seed))


def fleet_group(group: argparse.ArgumentParser) -> None:
    """`fleet`: the workers heard from, a cordon, and the loop."""
    under = group.add_subparsers(required=True)
    listing = under.add_parser("list", help="the roster")
    listing.add_argument("--fleet", default=None)
    listing.set_defaults(run=_knocking(fleet_list))
    for verb, runner in (("cordon", fleet_cordon), ("uncordon", fleet_uncordon)):
        one_verb = under.add_parser(verb)
        one_verb.add_argument("worker")
        one_verb.set_defaults(run=_knocking(runner))
    loop = under.add_parser("loop", help="the fleet kept at its target, tick by tick")
    loop.add_argument(
        "--cloud", required=True, help="the script: create <name>, delete <name>, list"
    )
    loop.add_argument("--seats", type=int, required=True, help="PINECALL_MAX_JOBS of the image")
    loop.add_argument("--fleet", default=None, help="one fleet alone; unset, every worker")
    loop.add_argument("--target", type=float, default=Line.target)
    loop.add_argument("--min", type=int, default=Line.at_least)
    loop.add_argument("--max", type=int, default=Line.at_most)
    loop.add_argument(
        "--grow-at-most",
        dest="grow_at_most",
        type=int,
        default=Line.grow_at_most,
        help="the most machines one tick asks for",
    )
    loop.add_argument("--every", type=float, default=15.0)
    loop.add_argument("--once", action="store_true")
    loop.add_argument("--dry-run", dest="dry_run", action="store_true")
    loop.set_defaults(run=_knocking(fleet_loop))


# Printed to stdout once, where the unit that mints it seals it: never to a terminal on a box.
def fleet_key(settings: Settings, args: argparse.Namespace) -> int:
    """Mint the fleet key of a world, in the box's own org."""
    env = parse_env(str(args.env))
    issued = key_table.Issued(
        org=DEFAULT_ORG, env=env, scopes=frozenset({THE_FLEET}), label=A_FLEET_KEY.format(env=env)
    )
    sys.stdout.write(asyncio.run(_minted(settings, issued)))
    return 0


# It is told every org's hosted apps and handed their tokens and secrets: minted like the fleet's.
def runner_key(settings: Settings, args: argparse.Namespace) -> int:
    """Mint the runner key of a world, in the box's own org."""
    env = parse_env(str(args.env))
    issued = key_table.Issued(
        org=DEFAULT_ORG,
        env=env,
        scopes=frozenset({THE_RUNNER}),
        label=A_RUNNER_KEY.format(env=env),
    )
    sys.stdout.write(asyncio.run(_minted(settings, issued)))
    return 0


def init(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org made or found, its first person invited and made an operator, the link once."""
    slug = str(args.org)
    try:
        org = _object(_answered(client.post(ORGS, json={"slug": slug, "name": args.name})))
        _line_out(f"{org['id']}  {org['slug']}  {org['name']}")
        named = str(org["id"])
    except GatewayRefused as refused:
        if ALREADY not in str(refused):
            raise
        _line_out(f"org {slug} is already there")
        named = slug
    body = {"email": args.email, "name": args.person, "role": args.role}
    invited = _object(_answered(client.post(f"{ORGS}/{named}/members", json=body)))
    member = _object(invited["member"])
    client.put(f"{ORGS}/{named}/members/{member['id']}/operator", json={"operator": True})
    _line_out(f"{member['id']}  {member['email']}  {member['role']}  runs this box")
    card = str(invited.get("link") or "")
    if card:
        _line_out(f"  {card}")
    _line_out(WHAT_TO_DO_NEXT.format(url=_origin_of(card) or _base(client)))
    return 0


def orgs_list(client: httpx.Client, _args: argparse.Namespace) -> int:
    """Every org, oldest first."""
    for org in _rows(_answered(client.get(ORGS))):
        _line_out(f"{org['id']}  {org['slug']}  {org['name']}")
    return 0


def orgs_add(client: httpx.Client, args: argparse.Namespace) -> int:
    """A new org."""
    org = _object(_answered(client.post(ORGS, json={"slug": args.slug, "name": args.name})))
    _line_out(f"{org['id']}  {org['slug']}  {org['name']}")
    return 0


def orgs_invite(client: httpx.Client, args: argparse.Namespace) -> int:
    """A person invited into the org, the link printed once."""
    body = {"email": args.email, "name": args.name, "role": args.role}
    invited = _object(_answered(client.post(f"{ORGS}/{args.org}/members", json=body)))
    member = _object(invited["member"])
    _line_out(f"{member['id']}  {member['email']}  {member['role']}  {member['status']}")
    if invited.get("link"):
        _line_out(f"  {invited['link']}")
    else:
        _line_out(
            "  already a person on this box: seated, they sign in with the password they have"
        )
    return 0


def orgs_operator(client: httpx.Client, args: argparse.Namespace) -> int:
    """A member of the org made an operator of the box, or taken back."""
    member = _member_by_email(client, args.org, args.email)
    path = f"{ORGS}/{args.org}/members/{member['id']}/operator"
    changed = _object(_answered(client.put(path, json={"operator": not args.revoke})))
    runs = "runs this box" if changed["operator"] else "no longer runs this box"
    _line_out(f"{changed['id']}  {changed['email']}  {runs}")
    return 0


def orgs_remove_member(client: httpx.Client, args: argparse.Namespace) -> int:
    """A person out of the org for good."""
    member = _member_by_email(client, args.org, args.email)
    _answered(client.delete(f"{ORGS}/{args.org}/members/{member['id']}"))
    _line_out(f"{member['email']} is out of {args.org}")
    return 0


def orgs_move(client: httpx.Client, args: argparse.Namespace) -> int:
    """An agent's logs and numbers moved into the org."""
    moved = _object(_answered(client.put(f"{ORGS}/{args.org}/agents", json={"agent": args.agent})))
    _line_out(
        f"{moved['agent']} -> {moved['org']}: {moved['logs']} logs, numbers {moved['numbers']}"
    )
    for number in _list(moved["stayed"]):
        _line_out(f"  {number} stayed: the org already answers at it")
    return 0


def orgs_rm(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org forgotten."""
    _answered(client.delete(f"{ORGS}/{args.org}"))
    _line_out(f"org {args.org} is gone")
    return 0


def orgs_quota(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org's limits in one world, replaced whole."""
    limits = {name: getattr(args, name) for name in QUOTAS if getattr(args, name) is not None}
    lent = None if args.lends is None else str(args.lends)
    lends = None if lent is None else ([] if lent == "none" else lent.split(","))
    body = {
        "env": args.env,
        "quotas": {"limits": limits, "budget_usd": args.budget_usd, "lends": lends},
    }
    quotas = _object(_answered(client.put(f"{ORGS}/{args.org}/quotas", json=body)))
    _line_out(json.dumps(quotas, indent=2, sort_keys=True))
    return 0


def orgs_dialling(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org's dial guards, replaced whole."""
    body: JsonObject = {
        "dial_anywhere": args.dial_anywhere,
        "per_minute": args.per_minute,
        "per_day": args.per_day,
        "max_duration_s": args.max_duration_s,
    }
    guards = _object(_answered(client.put(f"{ORGS}/{args.org}/dialling", json=body)))
    _line_out(json.dumps(guards, sort_keys=True))
    return 0


def orgs_sso(client: httpx.Client, args: argparse.Namespace) -> int:
    """Which provider the org signs in with; `--off` lets a password open it again."""
    path = f"{ORGS}/{args.org}/sso"
    if args.off:
        wired = _object(_answered(client.put(f"{path}/required", json={"required": False})))
    else:
        wired = _object(_answered(client.get(path)))
    if not wired["configured"]:
        _line_out(f"{args.org} signs in with no identity provider")
        return 0
    required = "required" if wired["required"] else "a password opens it too"
    _line_out(f"{wired['issuer']}  client {wired['client_id']}  {required}")
    return 0


def provider_key_set(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org's own key for a vendor, read from stdin: argv is what `ps` shows."""
    key = sys.stdin.readline().strip()
    _answered(client.put(f"{ORGS}/{args.org}/provider-keys/{args.vendor}", json={"key": key}))
    _line_out(f"{args.org} runs {args.vendor} on its own key from the next call")
    return 0


def provider_key_rm(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org's key for a vendor taken back."""
    _answered(client.delete(f"{ORGS}/{args.org}/provider-keys/{args.vendor}"))
    _line_out(f"{args.org} runs {args.vendor} on the box's key from the next call")
    return 0


def provider_key_list(client: httpx.Client, args: argparse.Namespace) -> int:
    """The vendors the org brought its own key for."""
    listed = _object(_answered(client.get(f"{ORGS}/{args.org}/provider-keys")))
    for vendor in _list(listed["vendors"]):
        _line_out(str(vendor))
    return 0


def keys_issue(client: httpx.Client, args: argparse.Namespace) -> int:
    """A key of the org in a world, printed once."""
    body: JsonObject = {
        "env": args.env,
        "label": args.label,
        "scopes": args.scope or None,
        "subject": args.subject,
        "name": args.name,
    }
    minted = _object(_answered(client.post(f"{ORGS}/{args.org}/keys", json=body)))
    scopes = ", ".join(str(scope) for scope in _list(minted["scopes"]))
    _line_out(str(minted["key"]))
    _line_out(f"  org {args.org} · {minted['env']} · {minted['label'] or '-'}")
    _line_out(f"  {scopes}")
    _line_out("  copy it now: the table keeps the fingerprint, and the key is never shown again")
    return 0


def keys_list(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org's keys by fingerprint, the revoked ones said."""
    for key in _rows(_answered(client.get(f"{ORGS}/{args.org}/keys"))):
        state = "revoked" if key["revoked_at"] else "live"
        _line_out(f"{key['fingerprint']}  {key['env'] or 'person'}  {key['label'] or '-'}  {state}")
    return 0


def keys_revoke(client: httpx.Client, args: argparse.Namespace) -> int:
    """One key stopped from the next request on."""
    _answered(client.post(f"/v1/ops/keys/{args.fingerprint}/revoke"))
    _line_out(f"{args.fingerprint} opens nothing from now on")
    return 0


def routes_list(client: httpx.Client, args: argparse.Namespace) -> int:
    """The org's routes in a world."""
    params = {"org": args.org, "env": args.env}
    for route in _rows(_answered(client.get("/v1/ops/routes", params=params))):
        _line_out(f"{route['number']}  {route['channel']}  {route['agent']}  {route['env']}")
    return 0


def routes_add(client: httpx.Client, args: argparse.Namespace) -> int:
    """A number answered by an agent; added again, it moves."""
    body = {
        "org": args.org,
        "number": args.number,
        "agent": args.agent,
        "channel": args.channel,
        "env": args.env,
    }
    route = _object(_answered(client.post("/v1/ops/routes", json=body)))
    _line_out(f"{route['number']}  {route['channel']}  {route['agent']}  {route['env']}")
    return 0


def routes_rm(client: httpx.Client, args: argparse.Namespace) -> int:
    """A route forgotten."""
    _answered(client.delete(f"/v1/ops/routes/{args.number}", params={"org": args.org}))
    _line_out(f"{args.number} answers nowhere now")
    return 0


def routes_seed(client: httpx.Client, args: argparse.Namespace) -> int:
    """A file of routes, each added: how a box comes up from a checkout."""
    rows = json.loads(Path(args.file).read_text(encoding="utf-8"))
    for row in _list(rows):
        route = _object(_answered(client.post("/v1/ops/routes", json=_object(row))))
        _line_out(f"{route['number']}  {route['channel']}  {route['agent']}  {route['env']}")
    return 0


def fleet_list(client: httpx.Client, args: argparse.Namespace) -> int:
    """The roster the gateway hears, one line per worker, and each fleet's totals."""
    listed = _object(_answered(client.get("/v1/ops/fleet")))
    now = float(str(listed["now"]))
    workers = [WorkerStatus.model_validate(row) for row in _list(listed["workers"])]
    wanted = [seat for seat in workers if args.fleet is None or seat.fleet == args.fleet]
    _line_out(f"{'fleet':18} {'worker':18} {'held':>4} {'seats':>5} {'load':>5}  standing   heard")
    for seat in wanted:
        _line_out(
            f"{seat.fleet:18} {seat.worker:18} {seat.active:>4} "
            f"{seat.max_jobs if seat.max_jobs is not None else 'cpu':>5} {seat.load:>5.2f}  "
            f"{_state_of(seat, workers, now):10} {now - seat.seen_at:.0f}s ago"
        )
    for summed in _rows(listed["totals"]):
        if args.fleet is not None and summed["fleet"] != args.fleet:
            continue
        full = "  FULL" if summed["full"] else ""
        _line_out(
            f"\n{summed['fleet']}: {summed['workers']} up · {summed['active']} calls · "
            f"{summed['free']} seats free · {summed['accepting']} accepting{full}"
        )
    return 0


def fleet_cordon(client: httpx.Client, args: argparse.Namespace) -> int:
    """A worker told, on its next heartbeat, to take no new call and leave."""
    _cordon(client, args.worker, on=True)
    _line_out(f"{args.worker} is cordoned: it finishes what it holds and leaves")
    return 0


def fleet_uncordon(client: httpx.Client, args: argparse.Namespace) -> int:
    """A worker's cordon taken back."""
    _cordon(client, args.worker, on=False)
    _line_out(f"{args.worker} takes calls again")
    return 0


# Stateless between ticks: the roster is the gateway's and the machines the cloud's, so a loop
# restarted resumes where the numbers are.
def fleet_loop(client: httpx.Client, args: argparse.Namespace) -> int:
    """Every `--every` seconds, one tick: the fleet kept at its target, or a dry run said."""
    if args.grow_at_most < 1 or args.target <= 0:
        raise DeclarationRefused(NO_GROWTH)
    cloud = Cloud(Path(args.cloud))
    line = Line(
        target=args.target,
        at_least=args.min,
        at_most=args.max,
        seats_per_worker=args.seats,
        grow_at_most=args.grow_at_most,
    )
    while True:
        now = time.time()
        seats = [seat for seat in _seats(client) if args.fleet is None or seat.fleet == args.fleet]
        machines = cloud.machines()
        decided = hub.decide(seats, machines, line, now)
        hub.printed(sys.stdout, hub.status_line(seats, machines, line, now, decided))
        for decision in decided:
            hub.printed(
                sys.stdout, f"  {hub.worded(decision)}{'  (dry run)' if args.dry_run else ''}"
            )
        if not args.dry_run:
            hub.applied(decided, lambda worker: _cordon(client, worker, on=True), cloud)
        if args.once:
            return 0
        time.sleep(args.every)


def _knocking(verb: Verb) -> Callable[[Settings, argparse.Namespace], int]:
    def run(settings: Settings, args: argparse.Namespace) -> int:
        if not settings.ops_key:
            raise PinecallError(NO_OPS_KEY)
        with httpx.Client(
            base_url=settings.gateway_url,
            headers={"Authorization": f"Bearer {settings.ops_key}"},
            timeout=TIMEOUT_S,
        ) as client:
            return verb(client, args)

    return run


def _answered(answer: httpx.Response) -> Json:
    if answer.is_success:
        return None if answer.status_code == NO_BODY else answer.json()
    detail = answer.text.strip()
    with contextlib.suppress(ValueError, KeyError, TypeError):
        detail = str(_object(answer.json())["detail"])
    raise GatewayRefused(f"{answer.status_code}: {detail}", answered=answer.status_code)


def _object(value: Json) -> JsonObject:
    if not isinstance(value, dict):
        raise GatewayRefused(f"the gateway answered {type(value).__name__}, not an object")
    return value


def _rows(value: Json) -> list[JsonObject]:
    return [_object(item) for item in _list(value)]


def _list(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise GatewayRefused(f"the gateway answered {type(value).__name__}, not a list")
    return value


# The box's public name, read off the link the gateway made on it; the client knows loopback only.
def _origin_of(card: str) -> str:
    parts = urlsplit(card)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _base(client: httpx.Client) -> str:
    return str(client.base_url).rstrip("/")


def _member_by_email(client: httpx.Client, org: str, email: str) -> JsonObject:
    listed = _object(_answered(client.get(f"{ORGS}/{org}/members")))
    for member in _rows(listed["members"]):
        if str(member["email"]).lower() == email.strip().lower():
            return member
    raise GatewayRefused(f"nobody in {org} has the address {email}", answered=404)


def _seats(client: httpx.Client) -> list[WorkerStatus]:
    listed = _object(_answered(client.get("/v1/ops/fleet")))
    return [WorkerStatus.model_validate(row) for row in _list(listed["workers"])]


def _cordon(client: httpx.Client, worker: str, *, on: bool) -> None:
    path = f"/v1/ops/fleet/{worker}/cordon"
    _answered(client.post(path) if on else client.delete(path))


def _state_of(seat: WorkerStatus, workers: list[WorkerStatus], now: float) -> str:
    return roster.worker_state(seat, [other for other in workers if other.fleet == seat.fleet], now)


async def _minted(settings: Settings, issued: key_table.Issued) -> str:
    pool = await open_pool(settings.database_url)
    try:
        _, secret = await key_table.issue(pool, issued)
    finally:
        await pool.close()
    return secret


def _line_out(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _limits_verbs(
    quota: argparse.ArgumentParser,
    dialling: argparse.ArgumentParser,
    signs: argparse.ArgumentParser,
    vendor_keys: argparse.ArgumentParser,
) -> None:
    quota.add_argument("org")
    quota.add_argument("--env", required=True, choices=("production", "sandbox"))
    for name in QUOTAS:
        quota.add_argument(f"--{name.replace('_', '-')}", dest=name, type=int, default=None)
    quota.add_argument("--budget-usd", dest="budget_usd", type=int, default=None)
    quota.add_argument("--lends", default=None, help="vendor[/model],… or none")
    quota.set_defaults(run=_knocking(orgs_quota))
    dialling.add_argument("org")
    dialling.add_argument(
        "--dial-anywhere", dest="dial_anywhere", action="store_true", default=None
    )
    dialling.add_argument("--no-dial-anywhere", dest="dial_anywhere", action="store_false")
    dialling.add_argument("--per-minute", dest="per_minute", type=int, default=None)
    dialling.add_argument("--per-day", dest="per_day", type=int, default=None)
    dialling.add_argument("--max-duration-s", dest="max_duration_s", type=int, default=None)
    dialling.set_defaults(run=_knocking(orgs_dialling))
    signs.add_argument("org")
    signs.add_argument("--off", action="store_true")
    signs.set_defaults(run=_knocking(orgs_sso))
    vendor = vendor_keys.add_subparsers(required=True)
    for verb, runner in (("set", provider_key_set), ("rm", provider_key_rm)):
        one_verb = vendor.add_parser(verb)
        one_verb.add_argument("org")
        one_verb.add_argument("vendor")
        one_verb.set_defaults(run=_knocking(runner))
    listing = vendor.add_parser("list")
    listing.add_argument("org")
    listing.set_defaults(run=_knocking(provider_key_list))
