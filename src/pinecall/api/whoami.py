"""GET /v1/whoami: the name on the key that knocked — the org, the key's id, and its label."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api._deps import KeyDep
from pinecall_protocol import WireModel

router = APIRouter()


class Whose(WireModel):
    """Who a key belongs to, as a terminal is allowed to read it: never the key, never its hash."""

    org: str
    key_id: str
    label: str | None = None


# The door `pinecall login` proves a key at and `pinecall whoami` asks every day: it takes the key
# every other tenant door takes, and answers the three words a person can check against their own.
@router.get("/v1/whoami")
async def whoami(key: KeyDep) -> Whose:
    """Whose key opened this door."""
    return Whose(org=key.org, key_id=key.key_id, label=key.label)
