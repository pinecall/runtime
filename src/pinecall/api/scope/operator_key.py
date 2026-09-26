"""Who the box's own doors open for: its key out of the environment, or a person it named."""

from __future__ import annotations

from hmac import compare_digest

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall.api.deps import KeysDep, MembersDep, SettingsDep
from pinecall.auth.bearer import bearer_of
from pinecall.auth.keys import KeyRecord, Keys
from pinecall.auth.members import Members
from pinecall.auth.visiting import the_operator, visiting

# Two things open /v1/ops, and neither is an org's admin. The BOX's key — `PINECALL_OPS_KEY`, out
# of the environment, belonging to no org, carrying no name — and a PERSON somebody holding that
# key marked as an operator (0020), whose own key opens their org's doors and these as well. The
# second exists because whoever owns the box is a person: without it they had to paste a systemd
# credential into a browser to open /admin, and the page could not say who was looking.
#
# An org's `admin` is NOT one of them by being an admin: an admin owns a tenant, an operator owns
# the machine every tenant is on, and the box would be handed over by the first org that invited
# itself one. The door refuses in words that say which of the two is missing.
NOT_THE_OPERATORS = "this door is the box's: its operator key, or a person the box made an operator"

OPS = "/v1/ops"


def an_operators_router() -> APIRouter:
    """A router of the box's own doors: under /v1/ops, every one of them behind an_operator."""
    return APIRouter(prefix=OPS, dependencies=[Depends(an_operator)])


async def an_operator(
    connection: HTTPConnection, settings: SettingsDep, keys: KeysDep, members: MembersDep
) -> None:
    """Whether the box's key knocked, or one of its people. Unset and nobody is the safe default."""
    bearer = bearer_of(connection.headers)
    if bearer is None:
        raise HTTPException(401, NOT_THE_OPERATORS, {"WWW-Authenticate": "Bearer"})
    if settings.ops_key and compare_digest(bearer, settings.ops_key):
        return
    if await _a_person_who_runs_the_box(bearer, keys, members):
        return
    raise HTTPException(401, NOT_THE_OPERATORS, {"WWW-Authenticate": "Bearer"})


# The key is verified exactly as every tenant door verifies one, then the PERSON it names is
# read: the flag is on a row and never on the key, so taking it back is one write and does not
# wait for a key to expire. A person is their email on this box (auth/members.py), so the key may
# be the one they hold in any org of theirs — or a visitor's, inside an org that is not
# (auth/visiting.py) — and the question is the same: does an active row of that address carry
# the flag today. A machine key names nobody and is nobody, whatever it may open.
async def _a_person_who_runs_the_box(bearer: str, keys: Keys, members: Members) -> bool:
    """Whether this key is a person's, and that person was made an operator of this box."""
    record = await keys.verify(bearer)
    return record is not None and await runs_the_box(record, members)


async def runs_the_box(record: KeyRecord, members: Members) -> bool:
    """Whether the person this key names — a member here, or a visitor — runs the box today."""
    if record.subject is None:
        return False
    email = visiting(record.subject)
    if email is None:
        member = await members.find(record.org, record.subject)
        if member is None or member.status != "active":
            return False
        email = member.email
    return await the_operator(members, email) is not None
