"""The one-use token that makes a member: how it is minted, how long it lives, what it is worth."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from pinecall.types import Member

# An invitation is a one-use token in a link, so it looks like one: a prefix nobody else uses and
# 192 bits after it. Like a key it is shown once and stored as its sha256 (auth/members.py keeps
# the table); unlike a key it dies on its own in a week, because a link in an inbox is a link
# somebody will forward. It is a RIGHT and not a credential: it buys one password and nothing else.
INVITATION_PREFIX = "inv_"
INVITATION_BYTES = 24
INVITATION_TTL_S = 7 * 24 * 3600.0


@dataclass(frozen=True)
class Invited:
    """A member just invited, and the token at the one moment it exists in the clear.

    No token at all when the email already belongs to a person on this box: they are seated
    active with the password they have, and there is nothing for a link to buy.
    """

    member: Member
    token: str | None
    expires_at: str | None


def new_invitation_token() -> str:
    """A one-use invitation, in the clear exactly once. Stored as its sha256, like a key."""
    return f"{INVITATION_PREFIX}{secrets.token_urlsafe(INVITATION_BYTES)}"
