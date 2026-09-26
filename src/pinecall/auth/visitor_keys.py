"""An operator inside an org they are no member of: what such a key names, and who still may."""

from __future__ import annotations

from datetime import datetime

from pinecall.auth.keys import Issued, KeyRecord, Keys, ListedKey
from pinecall.auth.members import Members, an_address
from pinecall.types import KEY_SCOPES, PRODUCTION, Env, Member

# A person's key names the member it was minted for (`subject = m_…`), and every door that asks
# "who is this" reads that row IN THE KEY'S ORG. An operator who opens a tenant's console is in an
# org where they have no row — and must not be given one: a row is a seat the tenant pays for, a
# line in its Team screen it never invited, and a person its admins could re-role. So the key
# names them by what they ARE on this box: `operator:<email>`. It is not a member id and cannot
# collide with one (`m_…`); it reads as itself wherever a subject is written down — the Keys
# screen, a dial's `asked_by`, a supervise verb — so what an operator did inside a tenant is
# attributable to a person by address, in that tenant's own log; and the corner, the pairing and
# the world switch, which all resolve a member of THIS org, find nobody and refuse.
VISITOR_PREFIX = "operator:"

# What the Keys screen of the org they walked into says about the key: whose it is, in words.
VISITOR_LABEL = "operator · {email}"


def a_visitor(email: str) -> str:
    """The subject a key carries when it is an operator's, inside an org they are no member of."""
    return f"{VISITOR_PREFIX}{an_address(email)}"


def visiting(subject: str | None) -> str | None:
    """The operator's address when this subject is a visitor's; None for a member's or nobody's."""
    if subject is None or not subject.startswith(VISITOR_PREFIX):
        return None
    return subject.removeprefix(VISITOR_PREFIX)


# The flag is on the ROW and never on the key (0020), here as at /v1/ops: an operator is whoever
# has an active row, in any org of this box, that the box marked. Oldest first, so the answer is
# the same row every time and a page can say which org they are an operator FROM.
async def the_operator(members: Members, email: str) -> Member | None:
    """The row that makes this address an operator of the box today, or None when none does."""
    for row in await members.orgs_of(email):
        if row.operator and row.status == "active":
            return row
    return None


# Revocation is checked on VERIFY, not done when the flag is taken back. Taking it back is one
# write with three causes — `orgs operator --revoke`, the member disabled, the member removed —
# and a revoke-on-write would have to be remembered at each of them and at the next one somebody
# adds; a key whose standing is read each time it knocks cannot be forgotten. It costs a visitor's
# key one indexed read per request and every other key nothing: the prefix is checked first.
class StandingKeys:
    """The keys table, asking of a visitor's key whether its person still runs the box."""

    def __init__(self, keys: Keys, members: Members) -> None:
        self._keys = keys
        self._members = members

    async def verify(self, key: str) -> KeyRecord | None:
        """The record behind the key; None too for a visitor whose operator flag is gone."""
        record = await self._keys.verify(key)
        if record is None:
            return None
        email = visiting(record.subject)
        if email is not None and await the_operator(self._members, email) is None:
            return None
        return record

    async def issue(
        self,
        org: str,
        label: str | None = None,
        *,
        env: Env = PRODUCTION,
        scopes: frozenset[str] = KEY_SCOPES,
        subject: str | None = None,
        name: str | None = None,
        created_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> Issued:
        """Straight through to the table."""
        return await self._keys.issue(
            org,
            label,
            env=env,
            scopes=scopes,
            subject=subject,
            name=name,
            created_by=created_by,
            expires_at=expires_at,
        )

    async def listed(self, org: str) -> tuple[ListedKey, ...]:
        """Straight through to the table."""
        return await self._keys.listed(org)

    async def revoke(self, hashed: str) -> bool:
        """Straight through to the table."""
        return await self._keys.revoke(hashed)

    async def touch(self, key_id: str) -> None:
        """Straight through to the table."""
        await self._keys.touch(key_id)
