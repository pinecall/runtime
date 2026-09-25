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

# Every column a Member is read from, spelled once: each SELECT and each RETURNING below hands
# back a whole row, so a column added to the shape is added here and nowhere else.
_A_ROW = """id, org, email, name, role, agents, status, operator, production, password_hash,
            created_at, verified_at"""

# (org, email) is UNIQUE, so a second invite of an accepted member is the conflict this INSERT
# steps around: the row is read first and the decision made in Python, where the sentence is.
_INSERT = """
INSERT INTO members (id, org, email, name, role, agents, status, production)
VALUES ($1, $2, $3, $4, $5, $6, 'invited', $7)
"""

_LISTED = f"SELECT {_A_ROW} FROM members WHERE org = $1 ORDER BY created_at, id"

# A stale mirror of the address — the person production removed and invited again — goes before
# production's row is written, or the UNIQUE (org, email) would refuse it. Two statements and no
# transaction, on purpose: the stale row is wrong whether or not the upsert after it succeeds.
_DISPLACED = "DELETE FROM members WHERE org = $1 AND email = $2 AND id <> $3"

# Production's row, by its id: inserted with no password and verified, or its production-owned
# fields written over — fenced by the org, so an id of another org's here is refused (empty).
_MIRRORED = f"""
INSERT INTO members (id, org, email, name, role, agents, status, verified_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, now())
    ON CONFLICT (id) DO UPDATE
   SET email = excluded.email, name = excluded.name, role = excluded.role,
       agents = excluded.agents, status = excluded.status,
       verified_at = COALESCE(members.verified_at, now())
 WHERE members.org = excluded.org
RETURNING {_A_ROW}
"""

# A seat is held by everybody the org has not disabled. The count is a query over the rows and
# never a counter column: the rows are the truth and a number kept beside them drifts from it.
_SEATED = "SELECT count(*) AS seated FROM members WHERE org = $1 AND status <> 'disabled'"

_FIND = f"SELECT {_A_ROW} FROM members WHERE org = $1 AND id = $2"

_BY_EMAIL = f"SELECT {_A_ROW} FROM members WHERE org = $1 AND email = $2"

# COALESCE is "a field left None keeps what it had", in the table's own words.
_UPDATE = f"""
UPDATE members
   SET role = COALESCE($3, role), agents = COALESCE($4, agents), status = COALESCE($5, status),
       production = COALESCE($6, production)
 WHERE org = $1 AND id = $2
RETURNING {_A_ROW}
"""

# A disabled member stays disabled: a link issued before they were is spent and opens nothing.
# `verified_at` is written when the link was vouched for (0048) and kept when it already was.
_ACTIVATE = f"""
UPDATE members
   SET status = 'active', password_hash = $2,
       verified_at = CASE WHEN $3 THEN COALESCE(verified_at, now()) ELSE verified_at END
 WHERE id = $1 AND status <> 'disabled'
RETURNING {_A_ROW}
"""

# A person who already exists on this box — and whose address is PROVED theirs — joins a second
# org seated: the row is active from the start, verified, and carries the hash they already have,
# so there is no link and no second password.
_INSERT_SEATED = """
INSERT INTO members (id, org, email, name, role, agents, status, password_hash, production,
                     verified_at)
VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, $8, now())
"""

# The newest hash any row of this email holds: the person's password, whichever org chose it.
_A_PERSONS_PASSWORD = """
SELECT password_hash
  FROM members
 WHERE email = $1 AND password_hash IS NOT NULL
 ORDER BY created_at DESC
 LIMIT 1
"""

_ORGS_OF = f"SELECT {_A_ROW} FROM members WHERE email = $1 ORDER BY created_at, id"

# Whether anybody other than an admin has proved this address: one row of theirs says when.
_VERIFIED = "SELECT 1 FROM members WHERE email = $1 AND verified_at IS NOT NULL LIMIT 1"

# One person, one password: a password chosen at an invitation lands on every row of theirs
# that has one. The rows still invited keep NULL — they are seated at login (`_JOIN`).
_PASSWORD_EVERYWHERE = """
UPDATE members SET password_hash = $2 WHERE email = $1 AND password_hash IS NOT NULL
"""

# A row invited before the person existed, seated at their first login to this org with the
# password they already have: fenced by the org and by the standing, so it seats nobody twice.
_JOIN = f"""
UPDATE members SET status = 'active', password_hash = $3
 WHERE org = $1 AND id = $2 AND status = 'invited'
RETURNING {_A_ROW}
"""

# An identity provider named the address: the row is active and proved, with no password on it,
# and a disabled member stays disabled here as everywhere.
_VOUCHED_FOR = f"""
UPDATE members SET status = 'active', verified_at = COALESCE(verified_at, now())
 WHERE org = $1 AND id = $2 AND status <> 'disabled'
RETURNING {_A_ROW}
"""

# The box's own write, and the only one that is not the org's: fenced by the org like every other
# read, so an id from one tenant cannot name a member of another.
_MAKE_OPERATOR = f"""
UPDATE members SET operator = $3 WHERE org = $1 AND id = $2
RETURNING {_A_ROW}
"""

# Fenced by the org like every other statement here. The invitations go with the row by their own
# ON DELETE CASCADE (0014); nothing else references a member, and what names one as text — a
# key's subject, a dial's asked_by, a log entry — keeps the id and simply names nobody.
_REMOVE = "DELETE FROM members WHERE org = $1 AND id = $2 RETURNING id"

_INVITE = """
INSERT INTO invitations (token_hash, member, expires_at, vouched) VALUES ($1, $2, $3, $4)
"""

# A re-invite spends every token still open for the member: the newest link is the only link.
_SPEND_OPEN = "UPDATE invitations SET spent_at = now() WHERE member = $1 AND spent_at IS NULL"

# Spent atomically, as one UPDATE with its WHERE: two browsers opening the same link at once get
# one member and one 404, never two members. It answers whether the link was vouched for, which
# is what decides if the row it activates is verified.
_SPEND = """
UPDATE invitations SET spent_at = now()
 WHERE token_hash = $1 AND spent_at IS NULL AND expires_at > now()
RETURNING member, vouched
"""


class PostgresMembers:
    """The two tables in Postgres, read on every request: a member disabled now is refused now."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def invite(
        self,
        org: str,
        email: str,
        name: str,
        role: Role,
        agents: Iterable[str],
        *,
        production: bool = False,
        vouched: bool = False,
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
                production=production,
            )
            known = await self.a_persons_password(email)
            if known is not None and await self.verified(email):
                member = replace(member, status="active", verified=True)
                await self._pool.execute(
                    _INSERT_SEATED,
                    member.id,
                    org,
                    email,
                    name,
                    role,
                    sorted(member.agents),
                    known,
                    production,
                )
                return Invited(member=member, token=None, expires_at=None)
            await self._pool.execute(
                _INSERT, member.id, org, email, name, role, sorted(member.agents), production
            )
        else:
            member = kept.member
            await self._pool.execute(_SPEND_OPEN, member.id)
        return await self._a_link_for(member, vouched)

    async def reset(self, org: str, id: str, *, vouched: bool = False) -> Invited | None:
        """The member read, every open link of theirs spent, and a new one written."""
        found = await self.find(org, id)
        if found is None or found.status != "active":
            return None
        await self._pool.execute(_SPEND_OPEN, id)
        return await self._a_link_for(found, vouched)

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """One UPDATE spends the token and names the member; a second makes them active; a
        third carries the password to every other org of theirs."""
        spent = await self._pool.fetchrow(_SPEND, fingerprint(token))
        if spent is None:
            return None
        row = await self._pool.fetchrow(
            _ACTIVATE, str(spent["member"]), password_hash, bool(spent["vouched"])
        )
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

    async def verified(self, email: str) -> bool:
        """One read across the orgs: whether any row of the address was proved."""
        return await self._pool.fetchrow(_VERIFIED, an_address(email)) is not None

    async def join(self, org: str, id: str, password_hash: str) -> Member | None:
        """One UPDATE, fenced by the org and by the standing."""
        row = await self._pool.fetchrow(_JOIN, org, id, password_hash)
        return None if row is None else a_member_of_row(row)

    async def vouched_for(self, org: str, id: str) -> Member | None:
        """One UPDATE, fenced by the org; a disabled row answers None."""
        row = await self._pool.fetchrow(_VOUCHED_FOR, org, id)
        return None if row is None else a_member_of_row(row)

    async def mirrored(self, member: Member) -> Member | None:
        """The stale row of the address out, then one upsert; an empty RETURNING is the id being
        another org's member here."""
        email = an_address(member.email)
        await self._pool.execute(_DISPLACED, member.org, email, member.id)
        row = await self._pool.fetchrow(
            _MIRRORED,
            member.id,
            member.org,
            email,
            member.name,
            member.role,
            sorted(member.agents),
            member.status,
        )
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
        production: bool | None = None,
    ) -> Member | None:
        """One UPDATE; NULL for a field left alone, which COALESCE turns into the column's own."""
        row = await self._pool.fetchrow(
            _UPDATE,
            org,
            id,
            role,
            None if agents is None else sorted(agents),
            status,
            production,
        )
        return None if row is None else a_member_of_row(row)

    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """One column, fenced by the org. The row is the truth about who runs this box."""
        row = await self._pool.fetchrow(_MAKE_OPERATOR, org, id, operator)
        return None if row is None else a_member_of_row(row)

    async def remove(self, org: str, id: str) -> bool:
        """One DELETE, fenced by the org; the row it returns says whether one went."""
        return await self._pool.fetchrow(_REMOVE, org, id) is not None

    async def _a_link_for(self, member: Member, vouched: bool) -> Invited:
        """One token for this member, a week long, its fingerprint and its standing in the table."""
        token = a_token()
        expires_at = datetime.fromtimestamp(time.time() + INVITATION_TTL_S, UTC)
        await self._pool.execute(_INVITE, fingerprint(token), member.id, expires_at, vouched)
        return Invited(member=member, token=token, expires_at=expires_at.isoformat())
