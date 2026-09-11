"""GET /v1/whoami: the name on the key that knocked — whose, where it opens, what it may do."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api._deps import KeyDep
from pinecall.types import Env
from pinecall_protocol import WireModel

router = APIRouter()


class Whose(WireModel):
    """Who a key belongs to, as a terminal is allowed to read it: never the key, never its hash."""

    org: str
    key_id: str
    label: str | None = None
    # The world this key opens, and what it may do there: what a console gates its sections by.
    env: Env
    scopes: list[str]
    # The person the key was minted for, when it is a person's; an org's own key names nobody.
    subject: str | None = None
    name: str | None = None


# The door `pinecall login` proves a key at and `pinecall whoami` asks every day: it takes the key
# every other tenant door takes, and answers the words a person can check against their own.
@router.get("/v1/whoami")
async def whoami(key: KeyDep) -> Whose:
    """Whose key opened this door, where it opens, and what it may do."""
    return Whose(
        org=key.org,
        key_id=key.key_id,
        label=key.label,
        env=key.env,
        scopes=sorted(key.scopes),
        subject=key.subject,
        name=key.name,
    )
