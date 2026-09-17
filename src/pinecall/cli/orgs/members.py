"""`pinecall-runtime orgs invite | operator | remove-member`: the box's hand in an org's people."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from pinecall.cli.operator import Operator
from pinecall.cli.orgs.sso import OPS_ORGS

# Nobody of that org answers to the email: the sentence names both, because a typo in either is
# the same mistake and the person reading has to know which one to fix.
NO_SUCH_MEMBER = "no member of {org} answers to {email}"


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
    found = await _the_member(org, email, operator)
    if found is None:
        return 1
    changed = await operator.put(
        f"{OPS_ORGS}/{org}/members/{found['id']}/operator", {"operator": running}
    )
    print(f"{changed['id']}  {changed['email']}  {changed['role']}", file=out)
    print(f"  {RUNS_THE_BOX if running else RUNS_NO_MORE}", file=out)
    return 0


# For good, and said so: `disabled` is the org's own way to stop somebody for now, from its own
# console. This is the row gone — for the address seated by mistake, the person who left.
REMOVED = "removed for good: every key of theirs is revoked, and the seat is free"


async def remove_member(org: str, email: str, operator: Operator, out: TextIO = sys.stdout) -> int:
    """The member with that email out of the org. The door refuses the org's last active admin."""
    found = await _the_member(org, email, operator)
    if found is None:
        return 1
    await operator.delete(f"{OPS_ORGS}/{org}/members/{found['id']}")
    print(f"{found['id']}  {found['email']}  {found['role']}", file=out)
    print(f"  {REMOVED}", file=out)
    return 0


async def _the_member(org: str, email: str, operator: Operator) -> dict[str, Any] | None:
    """The org's member with this address, or None and the sentence that names both on stderr."""
    people = await operator.get(f"{OPS_ORGS}/{org}/members")
    wanted = email.strip().lower()
    found: dict[str, Any] | None = next(
        (one for one in people["members"] if one["email"] == wanted), None
    )
    if found is None:
        print(NO_SUCH_MEMBER.format(email=email, org=org), file=sys.stderr)
    return found
