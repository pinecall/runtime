"""What a door that seats somebody asks first: may this key grant that, and whose is the link."""

from __future__ import annotations

from fastapi import HTTPException

from pinecall.auth.grants import check_may_grant
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members
from pinecall.types import Role


# A key grants what it holds and no more (auth/grants.py); a 403 in that sentence. Asked by the
# invitation, the row's PATCH, and the role an identity provider seats a stranger with.
async def may_grant(
    key: KeyRecord, members: Members, role: Role | None, production: bool | None
) -> None:
    """403 when this key may not hand out that role, or production access it has not got."""
    try:
        await check_may_grant(key, members, role, production)
    except PermissionError as refused:
        raise HTTPException(403, str(refused)) from refused


# Whether this address is a person of some OTHER org on this box too. The link an invitation or a
# reset answers is handed to the admin who asked, and a link buys a password — the person's one
# password, in every org of theirs. So it is handed over only for an address that is this org's
# alone; for anybody else's colleague it is posted to the address, and to nobody else (`mailed`).
# And a link that was handed over proves nothing about the address, so only one that travels by
# mail alone vouches for it (0048): the two answers are the one question, asked once.
async def is_member_elsewhere(members: Members, org: str, email: str) -> bool:
    """Whether the email has a row in an org that is not this one."""
    return any(row.org != org for row in await members.orgs_of(email))
