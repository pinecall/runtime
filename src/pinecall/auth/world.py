"""The world a request runs in: a person's names it, a server's token carries its own."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from pinecall.auth.corner import in_the_corner_asked
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members
from pinecall.auth.visiting import VISITOR_PREFIX
from pinecall.types import ENVS, PRODUCTION, SANDBOX, Env

# A person holds ONE key and works in both worlds: the request says which, and a request that
# says nothing is the sandbox's — what is being written, never what answers the phone. Production
# is the member's to open (`Member.opens_production`, 0039), read at every request so an admin
# taking it away closes the very next one. A key that names nobody is a server's token: it was
# made for one world and stays in it, so a header asking for the other is a mistake said out
# loud, not a door that quietly answers from the wrong world. An operator visiting an org keeps
# the world their visiting key was made for.
ENV_HEADER = "pinecall-env"
NOT_A_WORLD = "pinecall-env is sandbox or production, not {asked!r}"
NO_PRODUCTION = "{name} has no production access: an admin gives it in Team"
ONE_WORLD = "this token was made for {world}: make one for {asked} in Tokens"


def a_person(record: KeyRecord) -> bool:
    """Whether this key is a member's own, and not a server's token or an operator's visit."""
    return record.subject is not None and not record.subject.startswith(VISITOR_PREFIX)


async def in_the_world_asked(
    record: KeyRecord, headers: Mapping[str, str], members: Members
) -> KeyRecord:
    """The key as this request's world resolves it. Raises PermissionError with the sentence a
    door answers 403 with."""
    asked = headers.get(ENV_HEADER)
    if asked is not None and asked not in ENVS:
        raise PermissionError(NOT_A_WORLD.format(asked=asked))
    if not a_person(record):
        if asked is not None and asked != record.env:
            raise PermissionError(ONE_WORLD.format(world=record.env, asked=asked))
        return record
    world: Env = PRODUCTION if asked == PRODUCTION else SANDBOX
    if world == PRODUCTION and not await opens_production(record, members):
        raise PermissionError(NO_PRODUCTION.format(name=record.name or "this person"))
    return record if record.env == world else replace(record, env=world)


async def opens_production(record: KeyRecord, members: Members) -> bool:
    """Whether the person this key names may act in production now: an active row that says so."""
    member = None if record.subject is None else await members.find(record.org, record.subject)
    return member is not None and member.status == "active" and member.opens_production


async def as_asked(record: KeyRecord, headers: Mapping[str, str], members: Members) -> KeyRecord:
    """The key in the world, then the corner, this request asked for — every door's one read."""
    return await in_the_corner_asked(
        await in_the_world_asked(record, headers, members), headers, members
    )
