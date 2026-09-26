"""The world a request runs in: the instance's own, and a request that says another is refused."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from pinecall._settings import Settings
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members
from pinecall.auth.request_scope import in_the_corner_asked
from pinecall.auth.visitor_keys import VISITOR_PREFIX
from pinecall.types import ENVS, PRODUCTION

# An instance IS one world (`PINECALL_WORLD`): its own database, worker and keys. So the header no
# longer chooses anything — it is what the client BELIEVES it is talking to, and a belief that is
# wrong is said out loud instead of being answered from the wrong world: a caller that meant the
# sandbox and was served by production would be told it wrote what it did not write. The sentence
# names where the other world answers when this instance knows (`PINECALL_ELSEWHERE_URL`).
ENV_HEADER = "pinecall-env"
NOT_A_WORLD = "pinecall-env is sandbox or production, not {asked!r}"
NOT_THIS_WORLD = "this gateway is {here}'s, not {asked}'s: {asked} answers at {elsewhere}"
# A person's key with no header meant the sandbox until the sandbox became an instance of its own,
# so every CLI and app that predates the change would now write production on the same URL and key
# without knowing. At production a person says which world they mean, or nothing runs.
SAY_THE_WORLD = (
    "this is production, and a person's key says the world it means (pinecall-env): the sandbox"
    " answers at {elsewhere} — update pinecall"
)
NO_PRODUCTION = "{name} has no production access: an admin gives it in Team"
# A server's token was made for one world and stays in it: at the other instance it is refused
# rather than quietly re-read, because the rows it names live in the other database.
ONE_WORLD = "this token was made for {world}, and this gateway is {here}'s: use it at {elsewhere}"
# When the instance was not told where the other world answers, the sentence still says what to do.
THE_OTHER_GATEWAY = "the other gateway"


def a_person(record: KeyRecord) -> bool:
    """Whether this key is a member's own, and not a server's token or an operator's visit."""
    return record.subject is not None and not record.subject.startswith(VISITOR_PREFIX)


# The one reading of what a request SAYS, shared by both below: a header is an assertion about the
# instance, and a server's token belongs to one. What is left is the person, whom each reading
# treats in its own way.
def _held_against_the_instance(
    record: KeyRecord, headers: Mapping[str, str], settings: Settings
) -> str | None:
    """The world the request named, if any, once it holds; PermissionError when it does not."""
    here, elsewhere = settings.world, settings.elsewhere_url or THE_OTHER_GATEWAY
    asked = headers.get(ENV_HEADER)
    if asked is not None and asked not in ENVS:
        raise PermissionError(NOT_A_WORLD.format(asked=asked))
    if asked is not None and asked != here:
        raise PermissionError(NOT_THIS_WORLD.format(here=here, asked=asked, elsewhere=elsewhere))
    if not a_person(record) and record.env != here:
        raise PermissionError(ONE_WORLD.format(world=record.env, here=here, elsewhere=elsewhere))
    return asked


def _here(record: KeyRecord, settings: Settings) -> KeyRecord:
    """The key re-labelled with the instance's world, which a person's carries none of its own."""
    return record if record.env == settings.world else replace(record, env=settings.world)


# Production is the member's to open (`Member.opens_production`, 0039), read at every request so
# an admin taking it away closes the very next one; the sandbox is every member's. An operator
# visiting an org keeps the world their visiting key was made for, as a server's token does.
async def in_the_world_asked(
    record: KeyRecord, headers: Mapping[str, str], members: Members, settings: Settings
) -> KeyRecord:
    """The key as it ACTS in this instance's world. Raises PermissionError with the sentence a door
    answers 403 with: another world asked for, a token of the other one, production not opened."""
    asked = _held_against_the_instance(record, headers, settings)
    if a_person(record) and settings.world == PRODUCTION:
        if asked is None:
            elsewhere = settings.elsewhere_url or THE_OTHER_GATEWAY
            raise PermissionError(SAY_THE_WORLD.format(elsewhere=elsewhere))
        if not await opens_production(record, members):
            raise PermissionError(NO_PRODUCTION.format(name=record.name or "this person"))
    return _here(record, settings)


async def opens_production(record: KeyRecord, members: Members) -> bool:
    """Whether the person this key names may act in production now: an active row that says so."""
    member = None if record.subject is None else await members.find(record.org, record.subject)
    return member is not None and member.status == "active" and member.opens_production


async def as_asked(
    record: KeyRecord, headers: Mapping[str, str], members: Members, settings: Settings
) -> KeyRecord:
    """The key acting in the instance's world, then the corner this request asked for — the one
    read of every door that opens a scope."""
    return await in_the_corner_asked(
        await in_the_world_asked(record, headers, members, settings), headers, members
    )


async def as_itself(
    record: KeyRecord, headers: Mapping[str, str], members: Members, settings: Settings
) -> KeyRecord:
    """The key as an identity in the instance's world, then the corner asked — the read of the
    doors that open no scope: whoami, the login code, pairing, the org switch, one's own keys."""
    _held_against_the_instance(record, headers, settings)
    return await in_the_corner_asked(_here(record, settings), headers, members)
