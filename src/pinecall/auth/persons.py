"""A person's key: one per device, with what their role presets, and no world of its own."""

from __future__ import annotations

from pinecall.auth.keys import Issued, Keys
from pinecall.types import SANDBOX, Member


# A person's key from their member row — the invitation's, a password login's, a terminal's,
# another org's; a code spent for a browser copies the record it stood for. It carries no world:
# the request names one (auth/world.py) and the member's row says whether production opens
# (0039). The column still holds one, and it holds the sandbox, which is what a request that
# names none runs in. Its scopes are the role's, whole: a person who may act
# in production holds the agent there too, from `pinecall start --prod`.
async def a_persons_key(keys: Keys, member: Member, label: str | None) -> Issued:
    """A key for this member, labelled as the device or the door it was minted for."""
    return await keys.issue(
        org=member.org,
        label=label,
        env=SANDBOX,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
    )
