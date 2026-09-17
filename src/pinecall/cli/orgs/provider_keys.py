"""`pinecall-runtime orgs provider-key`: an org's own vendor keys, set, removed and listed."""

from __future__ import annotations

import sys
from typing import TextIO

from pinecall.cli.operator import Operator
from pinecall.cli.orgs.sso import OPS_ORGS

ON_THE_BOX = "this org runs every vendor on the keys of this box"
READ_THE_KEY = "paste the {vendor} key and press enter: "


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
