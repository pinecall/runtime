"""`pinecall-runtime keys`: the keys an org's workers and apps knock with — issue, list, revoke."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from typing import Any, TextIO

from pinecall.auth.keys import Issued, KeyRecord
from pinecall.cli.columns import as_columns
from pinecall.cli.operator import Operator, against_the_gateway, with_an_org

PURPOSE: str = "the org's API keys: issue | list | revoke"
VERBS: tuple[str, ...] = ("issue", "list", "revoke")

# The doors these verbs knock at, on PINECALL_OPS_KEY. An API key does not open them and it does
# not open an API key's doors: docs/decisions/keys.md says why the two are different things. A
# key is issued and listed under its org; a fingerprint already names one row, so revoke is not.
OPS_ORGS = "/v1/ops/orgs"
OPS_KEYS = "/v1/ops/keys"

# The one sentence that matters in the whole group. It is printed under every key ever issued,
# because the table stores the sha256 and there is no verb, here or anywhere, that reads one back.
PRINTED_ONCE = "copy it now: the table keeps the fingerprint, and the key is never shown again"

# What a revoked row still says. The row stays — see docs/decisions/keys.md — so the log entries
# that name this key stay readable.
REVOKED = "revoked"
LIVE = "live"

# A key with no label is a key nobody named. The column is still a column.
NO_LABEL = "—"


def configure(parser: argparse.ArgumentParser) -> None:
    """Three verbs, three parsers: each names its arguments, and `keys` alone prints them."""
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=f"{parser.prog} keys")

    issuing = verbs.add_parser("issue", help="a new key for an org, printed once and never again")
    issuing.add_argument("--label", default=None, help="what this key is for, for the listing")
    with_an_org(issuing)
    issuing.set_defaults(run=run_issue)

    listing = verbs.add_parser("list", help="every key of an org, by fingerprint, never by key")
    with_an_org(listing)
    listing.set_defaults(run=run_list)

    revoking = verbs.add_parser("revoke", help="stop honouring one key; its row and history stay")
    revoking.add_argument("fingerprint", metavar="<fingerprint>", help="as `keys list` prints it")
    revoking.set_defaults(run=run_revoke)

    parser.set_defaults(run=partial(_print_the_verbs, parser))


def run_issue(arguments: argparse.Namespace) -> int:
    """One key for one org. The plaintext reaches this terminal and no file, ever."""
    return against_the_gateway(partial(issue_key, arguments.org, arguments.label))


def run_list(arguments: argparse.Namespace) -> int:
    """Every key of one org, live and revoked, as fingerprints."""
    return against_the_gateway(partial(list_keys, arguments.org))


def run_revoke(arguments: argparse.Namespace) -> int:
    """One key stops opening doors from the next request. Nothing is deleted."""
    return against_the_gateway(partial(revoke_key, arguments.fingerprint))


# ── the verbs, as coroutines over an Operator a test can hand in ────────────────


async def issue_key(
    org: str, label: str | None, operator: Operator, out: TextIO = sys.stdout
) -> int:
    """Mint one key on the gateway's side and show it here, the once."""
    answer = await operator.post(f"{OPS_ORGS}/{org}/keys", {"label": label})
    print_the_key(_issued_of(answer), out)
    return 0


async def list_keys(org: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """Fingerprints, never keys. Nothing to list is a sentence, not an empty screen."""
    rows = await operator.get(f"{OPS_ORGS}/{org}/keys")
    if not rows:
        print(f"no keys in org {org} — `pinecall-runtime keys issue --org {org}`", file=out)
        return 0
    for line in as_columns([_row_of(key) for key in rows]):
        print(line, file=out)
    return 0


async def revoke_key(hashed: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """The row grows a `revoked_at` and stays where it is."""
    await operator.post(f"{OPS_KEYS}/{hashed}/revoke")
    print(
        f"{hashed} revoked — the row stays, so the log entries that name it stay readable", file=out
    )
    return 0


# The one printer of a key: the key alone on stdout, and the two lines about it on stderr. So
# stdout IS the key — a unit that pipes `keys issue` into `systemd-creds encrypt` gets nothing
# else — and a person at a terminal still reads all three lines.
def print_the_key(issued: Issued, out: TextIO, err: TextIO | None = None) -> None:
    """A key, once, on its own. The words about it go beside it, never into it."""
    record = issued.record
    beside = err or sys.stderr
    print(issued.key, file=out)
    print(f"  org {record.org} · {record.label or NO_LABEL}", file=beside)
    print(f"  {PRINTED_ONCE}", file=beside)


def _row_of(key: dict[str, Any]) -> tuple[str, ...]:
    """One key as a person reads it: what it hashes to, what it is for, and whether it still is."""
    return (
        str(key["fingerprint"]),
        str(key["label"] or NO_LABEL),
        REVOKED if key["revoked_at"] else LIVE,
    )


def _issued_of(answer: dict[str, Any]) -> Issued:
    """The door's answer as the one type this CLI prints. The key is in it exactly once."""
    return Issued(
        key=str(answer["key"]),
        record=KeyRecord(
            key_id=str(answer["key_id"]),
            org=str(answer["org"]),
            label=answer["label"] and str(answer["label"]),
        ),
    )


def _print_the_verbs(parser: argparse.ArgumentParser, _arguments: Any) -> int:
    """`keys` with no verb: say what there is, and exit as a help screen does."""
    parser.print_help()
    return 0
