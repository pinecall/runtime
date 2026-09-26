"""`pinecall-runtime orgs sso`: which provider an org signs in with, and the break-glass."""

from __future__ import annotations

import sys
from typing import TextIO

from pinecall.cli.operator import OPS_ORGS, Operator

# The door every verb of this group knocks at, on PINECALL_OPS_KEY. It is spelled here, in the
# leaf beside them, because `verbs` imports this module and a constant in both would be two.
# The break-glass, and the only write this verb makes. An org that turned `required` on and then
# lost its provider — a tenant renamed at Entra, a secret rotated on a Friday — has nobody left
# who can turn it off from the inside: the admin who would is exactly the person locked out. So
# the BOX may, and only in this direction; turning it back on is the org's own door.
BREAK_GLASS = "let this org's people log in with a password again while their provider is down"

# The two ends of that switch, said where an operator reads what they just did.
A_PASSWORD_AGAIN = "their people may log in with a password again: the provider is optional now"
THE_PROVIDER_ONLY = "their people sign in with that provider only: a password opens nothing"
NO_PROVIDER = "org {org} signs in with no identity provider: its people use a password"

# What an org auto-provisions nobody as. The column is a role or nothing, and nothing is the
# default: an address no admin invited is refused rather than seated.
SEATS_NOBODY = "nobody it was not told to"


async def sso(org: str, off: bool, operator: Operator, out: TextIO = sys.stdout) -> int:
    """Which provider one org signs in with, and whether a password still opens it."""
    door = f"{OPS_ORGS}/{org}/sso"
    said = await operator.put(f"{door}/required", {"required": False}) if off else None
    said = said or await operator.get(door)
    if not said.get("configured"):
        print(NO_PROVIDER.format(org=org), file=out)
        return 0
    print(f"{org}  {said['issuer']}  {said['client_id']}", file=out)
    print(f"  domains  {', '.join(said['domains'])}", file=out)
    print(f"  seats    {said['role'] or SEATS_NOBODY}", file=out)
    print(f"  {THE_PROVIDER_ONLY if said['required'] else A_PASSWORD_AGAIN}", file=out)
    return 0
