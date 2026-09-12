"""Who the box's own doors open for: its key out of the environment, or a person it named."""

from __future__ import annotations

from hmac import compare_digest

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from pinecall.api._deps import KeysDep, MembersDep, SettingsDep
from pinecall.auth.bearer import bearer_of
from pinecall.auth.keys import Keys
from pinecall.auth.members import Members

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


# The key is verified exactly as every tenant door verifies one, then the MEMBER it was minted for
# is read: the flag is on the row and never on the key, so taking it back is one write and does not
# wait for a key to expire. A machine key names nobody and is nobody, whatever it may open.
async def _a_person_who_runs_the_box(bearer: str, keys: Keys, members: Members) -> bool:
    """Whether this key is a person's, and that person was made an operator of this box."""
    record = await keys.verify(bearer)
    if record is None or record.subject is None:
        return False
    member = await members.find(record.org, record.subject)
    return member is not None and member.operator and member.status == "active"
