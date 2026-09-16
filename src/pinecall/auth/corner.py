"""Whose doors a request resolves in: a tenant's own corner, or the call's when the fleet knocks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from pinecall.auth.keys import KeyRecord, held_by, is_the_fleets, sees_every_corner
from pinecall.auth.members import Members
from pinecall.types import DeclarationRefused, Env, is_a_deployment

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


# An admin opening a developer's copy from the console: the same doors, in that developer's
# corner. Only a key that sees every corner may, only in the sandbox — production has no corners
# — and only into a member of its own org. Said in one sentence for every refusal.
CANNOT_LOOK_THERE = "only a key that sees every corner opens a colleague's, and only in the sandbox"


def looking_into(record: KeyRecord, holder: str) -> KeyRecord:
    """The same key, resolving this request in the corner named. Raises on a key that may not."""
    if is_a_deployment(record.env) or not sees_every_corner(record):
        raise PermissionError(CANNOT_LOOK_THERE)
    return replace(record, looking_at=holder)


# The header the console sends when an admin opens a developer's copy: that member's id. Every
# door that verifies a key — the scoped deps, the reader of a log — passes its record through
# here, so each answers in that corner. Refused in one sentence when the key may not, and in
# another when the id names nobody active in the org.
CORNER_HEADER = "pinecall-corner"
NOT_A_COLLEAGUE = "no active member of this org answers to that corner"


async def in_the_corner_asked(
    record: KeyRecord, headers: Mapping[str, str], members: Members
) -> KeyRecord:
    """The key as this request resolves it: its own corner, or the colleague's the header names.
    Raises PermissionError with the sentence a door answers 403 with."""
    corner = headers.get(CORNER_HEADER)
    if not corner or corner == record.subject:
        return record
    looking = looking_into(record, corner)
    seated = await members.find(record.org, corner)
    if seated is None or seated.status != "active":
        raise PermissionError(NOT_A_COLLEAGUE)
    return looking
