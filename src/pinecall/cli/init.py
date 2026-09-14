"""`pinecall-runtime init`: the first org and the first person, so a fresh runtime has a way in."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from typing import Any, TextIO

from pinecall.cli.operator import Operator, OperatorRefused, against_the_gateway
from pinecall.types import ROLES

PURPOSE: str = "the first org and the first person on a runtime nobody has used yet"

# The doors, on PINECALL_OPS_KEY — the same two `orgs add` and `orgs invite` knock at. This verb
# is those two in one, because the first thing anybody does is both of them and the order matters.
OPS_ORGS = "/v1/ops/orgs"

# A slug already there is not a failure: `init` is the verb somebody runs twice while reading the
# README, and the second run should carry on to the person rather than stop at the org.
ALREADY = "an org already answers to the slug"

# The first person is an ADMIN of their org and an operator of this box: somebody has to be able
# to make the second org, and on a fresh runtime there is nobody else to do it.
THE_FIRST_ROLE = "admin"

WHAT_TO_DO_NEXT = """
  Open the link above to set a password. Then, in the directory of an agent:

    pinecall login {url}
    pinecall run
"""


def configure(parser: argparse.ArgumentParser) -> None:
    """No verbs: `init` does one thing, and its flags are who the first person is."""
    parser.add_argument("--org", required=True, metavar="<slug>", help="lowercase, digits, dashes")
    parser.add_argument("--email", required=True, help="the first person's address")
    parser.add_argument("--person", required=True, metavar="<name>", help="what to call them")
    parser.add_argument("--name", default=None, help="what to call the org (default: the slug)")
    parser.add_argument(
        "--role",
        default=THE_FIRST_ROLE,
        choices=sorted(ROLES),
        help=f"default {THE_FIRST_ROLE}",
    )
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Make the org, invite the person, make them an operator, and say what to do next."""
    return against_the_gateway(
        partial(
            started,
            arguments.org,
            arguments.name,
            arguments.email,
            arguments.person,
            arguments.role,
        )
    )


async def started(
    slug: str,
    name: str | None,
    email: str,
    person: str,
    role: str,
    operator: Operator,
    out: TextIO = sys.stdout,
) -> int:
    """One org, one person who can log in, and one line saying where to point a terminal."""
    org = await _the_org(slug, name, operator, out)
    said = await operator.post(
        f"{OPS_ORGS}/{org}/members", {"email": email, "name": person, "role": role}
    )
    member: dict[str, Any] = said["member"]
    # And an operator of the box. Somebody has to be able to make the SECOND org, and on a fresh
    # runtime there is nobody else who could be given that: the ops key is a box secret, not a
    # person. `orgs operator <org> <email> --no` takes it back.
    await operator.put(f"{OPS_ORGS}/{org}/members/{member['id']}/operator", {"operator": True})
    print(f"{member['id']}  {member['email']}  {member['role']}  runs this box", file=out)
    print(f"  {operator.base}/invitations/{said['token']}", file=out)
    print(WHAT_TO_DO_NEXT.format(url=operator.base), file=out)
    return 0


async def _the_org(slug: str, name: str | None, operator: Operator, out: TextIO) -> str:
    """The org this runtime's first person belongs to, made or found. Its id, which rows name."""
    try:
        org: dict[str, Any] = await operator.post(OPS_ORGS, {"slug": slug, "name": name})
    except OperatorRefused as refused:
        if ALREADY not in str(refused):
            raise
        print(f"org {slug} is already there", file=out)
        return slug
    print(f"{org['id']}  {org['slug']}  {org['name']}", file=out)
    return str(org["id"])
