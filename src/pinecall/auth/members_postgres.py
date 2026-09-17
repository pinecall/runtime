"""The members table in Postgres: one row per (org, email), and the statements that read it."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime

from pinecall.auth.invitations import INVITATION_TTL_S, Invited, a_token
from pinecall.auth.keys import fingerprint
from pinecall.auth.members import Kept, a_member_id, a_member_of_row, an_address, text_or_none
from pinecall.log.store import Pool
from pinecall.types import Member, MemberStatus, Role

# (org, email) is UNIQUE, so a second invite of an accepted member is the conflict this INSERT
# steps around: the row is read first and the decision made in Python, where the sentence is.
_INSERT = """
INSERT INTO members (id, org, email, name, role, agents, status)
VALUES ($1, $2, $3, $4, $5, $6, 'invited')
"""

_LISTED = """
SELECT id, org, email, name, role, agents, status, operator, password_hash, created_at
  FROM members
 WHERE org = $1
 ORDER BY created_at, id
"""

# A seat is held by everybody the org has not disabled. The count is a query over the rows and
# never a counter column: the rows are the truth and a number kept beside them drifts from it.
_SEATED = "SELECT count(*) AS seated FROM members WHERE org = $1 AND status <> 'disabled'"

_FIND = """
SELECT id, org, email, name, role, agents, status, operator, password_hash, created_at
  FROM members
 WHERE org = $1 AND id = $2
"""

_BY_EMAIL = """
SELECT id, org, email, name, role, agents, status, operator, password_hash, created_at
  FROM members
 WHERE org = $1 AND email = $2
"""

# COALESCE is "a field left None keeps what it had", in the table's own words.
_UPDATE = """
UPDATE members
   SET role = COALESCE($3, role), agents = COALESCE($4, agents), status = COALESCE($5, status)
 WHERE org = $1 AND id = $2
RETURNING id, org, email, name, role, agents, status, operator, password_hash, created_at
"""

# A disabled member stays disabled: a link issued before they were is spent and opens nothing.
_ACTIVATE = """
UPDATE members SET status = 'active', password_hash = $2 WHERE id = $1 AND status <> 'disabled'
RETURNING id, org, email, name, role, agents, status, operator, password_hash, created_at
"""

# A person who already exists on this box joins a second org seated: the row is active from the
# start and carries the hash they already have, so there is no link and no second password.
_INSERT_SEATED = """
INSERT INTO members (id, org, email, name, role, agents, status, password_hash)
VALUES ($1, $2, $3, $4, $5, $6, 'active', $7)
"""

# The newest hash any row of this email holds: the person's password, whichever org chose it.
_A_PERSONS_PASSWORD = """
SELECT password_hash
  FROM members
 WHERE email = $1 AND password_hash IS NOT NULL
 ORDER BY created_at DESC
 LIMIT 1
"""

_ORGS_OF = """
SELECT id, org, email, name, role, agents, status, operator, password_hash, created_at
  FROM members
 WHERE email = $1
 ORDER BY created_at, id
"""

# One person, one password: a password chosen at an invitation lands on every row of theirs
# that has one. The rows still invited keep NULL — they are seated at login (`_JOIN`).
_PASSWORD_EVERYWHERE = """
UPDATE members SET password_hash = $2 WHERE email = $1 AND password_hash IS NOT NULL
"""

# A row invited before the person existed, seated at their first login to this org with the
# password they already have: fenced by the org and by the standing, so it seats nobody twice.
_JOIN = """
UPDATE members SET status = 'active', password_hash = $3
 WHERE org = $1 AND id = $2 AND status = 'invited'
RETURNING id, org, email, name, role, agents, status, operator, password_hash, created_at
"""

# The box's own write, and the only one that is not the org's: fenced by the org like every other
# read, so an id from one tenant cannot name a member of another.
_MAKE_OPERATOR = """
UPDATE members SET operator = $3 WHERE org = $1 AND id = $2
RETURNING id, org, email, name, role, agents, status, operator, password_hash, created_at
"""

_INVITE = "INSERT INTO invitations (token_hash, member, expires_at) VALUES ($1, $2, $3)"

# A re-invite spends every token still open for the member: the newest link is the only link.
_SPEND_OPEN = "UPDATE invitations SET spent_at = now() WHERE member = $1 AND spent_at IS NULL"

# Spent atomically, as one UPDATE with its WHERE: two browsers opening the same link at once get
# one member and one 404, never two members.
_SPEND = """
UPDATE invitations SET spent_at = now()
 WHERE token_hash = $1 AND spent_at IS NULL AND expires_at > now()
RETURNING member
"""


class PostgresMembers:
    """The two tables in Postgres, read on every request: a member disabled now is refused now."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def invite(
        self, org: str, email: str, name: str, role: Role, agents: Iterable[str]
    ) -> Invited | None:
        """The row when there is none yet, then the token; a still-invited member gets a new one."""
        email = an_address(email)
        kept = await self.by_email(org, email)
        if kept is not None and kept.member.status != "invited":
            return None
        if kept is None:
            member = Member(
                id=a_member_id(),
                org=org,
                email=email,
                name=name,
                role=role,
                agents=frozenset(agents),
            )
            known = await self.a_persons_password(email)
            if known is not None:
                member = replace(member, status="active")
                await self._pool.execute(
                    _INSERT_SEATED, member.id, org, email, name, role, sorted(member.agents), known
                )
                return Invited(member=member, token=None, expires_at=None)
            await self._pool.execute(
                _INSERT, member.id, org, email, name, role, sorted(member.agents)
            )
        else:
            member = kept.member
            await self._pool.execute(_SPEND_OPEN, member.id)
        token = a_token()
        expires_at = datetime.fromtimestamp(time.time() + INVITATION_TTL_S, UTC)
        await self._pool.execute(_INVITE, fingerprint(token), member.id, expires_at)
        return Invited(member=member, token=token, expires_at=expires_at.isoformat())

    async def reset(self, org: str, id: str) -> Invited | None:
        """The member read, every open link of theirs spent, and a new one written."""
        found = await self.find(org, id)
        if found is None or found.status != "active":
            return None
        await self._pool.execute(_SPEND_OPEN, id)
        token = a_token()
        expires_at = datetime.fromtimestamp(time.time() + INVITATION_TTL_S, UTC)
        await self._pool.execute(_INVITE, fingerprint(token), id, expires_at)
        return Invited(member=found, token=token, expires_at=expires_at.isoformat())

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """One UPDATE spends the token and names the member; a second makes them active; a
        third carries the password to every other org of theirs."""
        spent = await self._pool.fetchrow(_SPEND, fingerprint(token))
        if spent is None:
            return None
        row = await self._pool.fetchrow(_ACTIVATE, str(spent["member"]), password_hash)
        if row is None:
            return None
        member = a_member_of_row(row)
        await self._pool.execute(_PASSWORD_EVERYWHERE, member.email, password_hash)
        return member

    async def a_persons_password(self, email: str) -> str | None:
        """One read across the orgs, newest hash first."""
        email = an_address(email)
        row = await self._pool.fetchrow(_A_PERSONS_PASSWORD, email)
        return None if row is None else text_or_none(row["password_hash"])

    async def orgs_of(self, email: str) -> tuple[Member, ...]:
        """Every row of this email, oldest first: what the console's org switch lists."""
        email = an_address(email)
        return tuple(a_member_of_row(row) for row in await self._pool.fetch(_ORGS_OF, email))

    async def join(self, org: str, id: str, password_hash: str) -> Member | None:
        """One UPDATE, fenced by the org and by the standing."""
        row = await self._pool.fetchrow(_JOIN, org, id, password_hash)
        return None if row is None else a_member_of_row(row)

    async def listed(self, org: str) -> tuple[Member, ...]:
        """Oldest first, disabled ones included: the row stays because the log names them."""
        return tuple(a_member_of_row(row) for row in await self._pool.fetch(_LISTED, org))

    async def seated(self, org: str) -> int:
        """One count over the org's rows: everybody it has not disabled."""
        row = await self._pool.fetchrow(_SEATED, org)
        return 0 if row is None else int(row["seated"])

    async def find(self, org: str, id: str) -> Member | None:
        """One read on the primary key, fenced by the org."""
        row = await self._pool.fetchrow(_FIND, org, id)
        return None if row is None else a_member_of_row(row)

    async def by_email(self, org: str, email: str) -> Kept | None:
        """One read on the UNIQUE pair, with the hash login checks against."""
        email = an_address(email)
        row = await self._pool.fetchrow(_BY_EMAIL, org, email)
        return (
            None if row is None else Kept(a_member_of_row(row), text_or_none(row["password_hash"]))
        )

    async def update(
        self,
        org: str,
        id: str,
        *,
        role: Role | None = None,
        agents: Iterable[str] | None = None,
        status: MemberStatus | None = None,
    ) -> Member | None:
        """One UPDATE; NULL for a field left alone, which COALESCE turns into the column's own."""
        row = await self._pool.fetchrow(
            _UPDATE, org, id, role, None if agents is None else sorted(agents), status
        )
        return None if row is None else a_member_of_row(row)

    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """One column, fenced by the org. The row is the truth about who runs this box."""
        row = await self._pool.fetchrow(_MAKE_OPERATOR, org, id, operator)
        return None if row is None else a_member_of_row(row)
