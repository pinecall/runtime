"""Whose doors a request resolves in: a tenant's own corner, or the call's when the fleet knocks."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.auth.keys import KeyRecord, held_by, is_the_fleets
from pinecall.types import DeclarationRefused, Env

# A tenant that names a corner that is not its own. Said in these words at every door that takes
# one, and never 404: whether that org exists is not the asker's business.
NOT_YOUR_CORNER = "this key works in its own org and world: only the fleet's key names another"


@dataclass(frozen=True)
class Corner:
    """The org, the world, and the holder's corner of it, that one request resolves in."""

    org: str
    env: Env
    holder: str | None = None


# Every door the worker knocks at asks this once: the tenant's key answers its own corner and is
# refused any other; the fleet's key answers the corner the request named, which is the call's —
# what the dispatch said. Nothing named means the key's own for both, so a fleet key on a laptop
# still reads as before, and so does every tenant key at every door.
def corner_of(
    record: KeyRecord,
    org: str | None = None,
    env: Env | None = None,
    holder: str | None = None,
) -> Corner:
    """The corner this key resolves in, or a refusal when a tenant named somebody else's."""
    own = Corner(record.org, record.env, held_by(record))
    if org is None and env is None and holder is None:
        return own
    asked = Corner(org or own.org, env or own.env, holder)
    if is_the_fleets(record):
        return asked
    if asked != own:
        raise DeclarationRefused(NOT_YOUR_CORNER)
    return own
