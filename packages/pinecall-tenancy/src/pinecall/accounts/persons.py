"""The person a key names: their active row, and the address they carry into every org."""

from __future__ import annotations

from pinecall.accounts.refusals import NOT_A_MEMBER, ONE_ORG_EACH, NotActive, NotAMembersKey
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members
from pinecall.auth.visitor_keys import operator_member, visitor_email
from pinecall.types import Member


# A visitor's key names no member of the org it opens, so the person is found the other way
# round: by the address the key carries, in the row that makes them an operator. That is what
# lets the org switch work from INSIDE a tenant — back home, or on to the next one.
async def person_of(key: KeyRecord, members: Members) -> Member:
    """The active member this key was minted for; refused for a machine key or a member gone."""
    if key.subject is None:
        raise NotAMembersKey(ONE_ORG_EACH)
    email = visitor_email(key.subject)
    member = (
        await members.find(key.org, key.subject)
        if email is None
        else await operator_member(members, email)
    )
    if member is None or member.status != "active":
        raise NotActive(NOT_A_MEMBER)
    return member


# A member's address is on their row; a visiting operator's is in the subject itself, since no
# row of that org is theirs (auth/visitor_keys.py). A machine's key has neither.
async def address_of(key: KeyRecord, members: Members) -> str | None:
    """The address of the person this key was minted for, or None for a key that names nobody."""
    visitor = visitor_email(key.subject)
    if visitor is not None:
        return visitor
    if key.subject is None:
        return None
    member = await members.find(key.org, key.subject)
    return None if member is None else member.email
