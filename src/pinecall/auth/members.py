"""The members table: who the people of an org are, their invitations, and what login reads."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from pinecall.auth.invitations import INVITATION_TTL_S, Invited, a_token
from pinecall.auth.keys import fingerprint
from pinecall.log.store import Pool
from pinecall.types import Member, MemberStatus, Role, a_role

# The row's own name, not a secret: it is what a key's `subject` carries and what a seat says.
MEMBER_ID_PREFIX = "m_"
MEMBER_ID_BYTES = 6


@dataclass(frozen=True)
class Kept:
    """What the login door reads about a person: who they are, and the hash their password left."""

    member: Member
    password_hash: str | None


class Members(Protocol):
    """Where the org's doors invite, list and change its people, and where login finds one."""

    async def invite(
        self, org: str, email: str, name: str, role: Role, agents: Iterable[str]
    ) -> Invited | None:
        """A new member with a one-use token, or a fresh token for one still invited. None when
        the email already belongs to a member who accepted: they log in, they are not re-invited."""
        ...

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """The invitation spent and the member active with this password. None when no open,
        unexpired invitation answers to the token."""
        ...

    async def listed(self, org: str) -> tuple[Member, ...]:
        """Every member of the org, oldest first, disabled ones included."""
        ...

    # What the `seats` quota is measured against. Invited counts: an invitation sent is a seat
    # taken, or an org at its limit could invite forever and seat them all the moment they
    # accepted. A disabled member keeps their row and holds none, which is what frees one.
    async def seated(self, org: str) -> int:
        """How many people of this org hold a seat: invited and active, never disabled."""
        ...

    async def find(self, org: str, id: str) -> Member | None:
        """One member of this org by id, or None."""
        ...

    async def by_email(self, org: str, email: str) -> Kept | None:
        """The person login is about, with the hash to check against, or None."""
        ...

    async def update(
        self,
        org: str,
        id: str,
        *,
        role: Role | None = None,
        agents: Iterable[str] | None = None,
        status: MemberStatus | None = None,
    ) -> Member | None:
        """The member with these fields replaced; a field left None keeps what it had. None when
        no member of this org answers to the id."""
        ...

    # Not part of `update`, on purpose: everything there is the ORG's to change, on a key with
    # `team`. This one is the BOX's, on the ops key, and putting it in the same call would be one
    # body away from an org promoting its own admin to run the machine it is a tenant on.
    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """This person runs the box, or stops. None when no member of this org answers to the id."""
        ...


@dataclass(frozen=True)
class _Row:
    member: Member
    password_hash: str | None
    created_at: str


@dataclass(frozen=True)
class _Invitation:
    member: str
    expires_at: float
    spent: bool = False


class MemoryMembers:
    """The members of a process with no database: a dev clone invites, and forgets when it exits."""

    # Rows to start with, as MemoryKeys takes records: a clone seeded from a fixture, and a test
    # about a key whose subject must name somebody. They have no password and so cannot log in;
    # a member who can is one who accepted an invitation, here as in Postgres.
    def __init__(
        self, rows: Iterable[Member] = (), *, clock: Callable[[], float] = time.time
    ) -> None:
        self._clock = clock
        self._rows: dict[str, _Row] = {
            member.id: _Row(member, None, _at(clock())) for member in rows
        }
        self._invitations: dict[str, _Invitation] = {}

    async def invite(
        self, org: str, email: str, name: str, role: Role, agents: Iterable[str]
    ) -> Invited | None:
        """One row per (org, email); a second invite of one still invited replaces the token."""
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
            self._rows[member.id] = _Row(member, None, _at(self._clock()))
        else:
            member = kept.member
            for hashed, invitation in self._invitations.items():
                if invitation.member == member.id and not invitation.spent:
                    self._invitations[hashed] = replace(invitation, spent=True)
        token, expires_at = a_token(), self._clock() + INVITATION_TTL_S
        self._invitations[fingerprint(token)] = _Invitation(member.id, expires_at)
        return Invited(member=member, token=token, expires_at=_at(expires_at))

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """Spend the token, then make the member active with this password."""
        hashed = fingerprint(token)
        invitation = self._invitations.get(hashed)
        if invitation is None or invitation.spent or invitation.expires_at <= self._clock():
            return None
        self._invitations[hashed] = replace(invitation, spent=True)
        row = self._rows[invitation.member]
        member = replace(row.member, status="active")
        self._rows[member.id] = replace(row, member=member, password_hash=password_hash)
        return member

    async def listed(self, org: str) -> tuple[Member, ...]:
        """In the order they were invited, which for a dict is the order they were inserted."""
        return tuple(row.member for row in self._rows.values() if row.member.org == org)

    async def seated(self, org: str) -> int:
        """The same count the WHERE below makes, over a dict: everybody but the disabled."""
        return sum(1 for row in await self.listed(org) if row.status != "disabled")

    async def find(self, org: str, id: str) -> Member | None:
        """By id, and only within the org: another org's member is nobody here."""
        row = self._rows.get(id)
        return None if row is None or row.member.org != org else row.member

    async def by_email(self, org: str, email: str) -> Kept | None:
        """The one row of this org with this email, as login reads it."""
        for row in self._rows.values():
            if row.member.org == org and row.member.email == email:
                return Kept(row.member, row.password_hash)
        return None

    async def update(
        self,
        org: str,
        id: str,
        *,
        role: Role | None = None,
        agents: Iterable[str] | None = None,
        status: MemberStatus | None = None,
    ) -> Member | None:
        """Replace what was named; the rest stays as it was."""
        found = await self.find(org, id)
        if found is None:
            return None
        member = replace(
            found,
            role=found.role if role is None else role,
            agents=found.agents if agents is None else frozenset(agents),
            status=found.status if status is None else status,
        )
        self._rows[id] = replace(self._rows[id], member=member)
        return member

    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """The same one column, over a dict."""
        found = await self.find(org, id)
        if found is None:
            return None
        member = replace(found, operator=operator)
        self._rows[id] = replace(self._rows[id], member=member)
        return member


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

_ACTIVATE = """
UPDATE members SET status = 'active', password_hash = $2 WHERE id = $1
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

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """One UPDATE spends the token and names the member; a second makes them active."""
        spent = await self._pool.fetchrow(_SPEND, fingerprint(token))
        if spent is None:
            return None
        row = await self._pool.fetchrow(_ACTIVATE, str(spent["member"]), password_hash)
        return None if row is None else _a_member(row)

    async def listed(self, org: str) -> tuple[Member, ...]:
        """Oldest first, disabled ones included: the row stays because the log names them."""
        return tuple(_a_member(row) for row in await self._pool.fetch(_LISTED, org))

    async def seated(self, org: str) -> int:
        """One count over the org's rows: everybody it has not disabled."""
        row = await self._pool.fetchrow(_SEATED, org)
        return 0 if row is None else int(row["seated"])

    async def find(self, org: str, id: str) -> Member | None:
        """One read on the primary key, fenced by the org."""
        row = await self._pool.fetchrow(_FIND, org, id)
        return None if row is None else _a_member(row)

    async def by_email(self, org: str, email: str) -> Kept | None:
        """One read on the UNIQUE pair, with the hash login checks against."""
        row = await self._pool.fetchrow(_BY_EMAIL, org, email)
        return None if row is None else Kept(_a_member(row), _text(row["password_hash"]))

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
        return None if row is None else _a_member(row)

    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """One column, fenced by the org. The row is the truth about who runs this box."""
        row = await self._pool.fetchrow(_MAKE_OPERATOR, org, id, operator)
        return None if row is None else _a_member(row)


def a_member_id() -> str:
    """A name for the row. It is what a person's key carries as `subject`."""
    return f"{MEMBER_ID_PREFIX}{secrets.token_hex(MEMBER_ID_BYTES)}"


def members_for(pool: Pool | None) -> Members:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryMembers() if pool is None else PostgresMembers(pool)


def _at(seconds: float) -> str:
    """A moment as Postgres hands its timestamps back: ISO 8601, UTC."""
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _text(column: Any) -> str | None:
    """A nullable text column: the string, or None when the row has none."""
    return None if column is None else str(column)


def _a_member(row: Any) -> Member:
    """One row back into the domain's own Member. The columns are its fields, name for name."""
    return Member(
        id=str(row["id"]),
        org=str(row["org"]),
        email=str(row["email"]),
        name=str(row["name"]),
        role=a_role(str(row["role"])),
        agents=frozenset(str(agent) for agent in row["agents"]),
        status=_a_status(str(row["status"])),
        operator=bool(row["operator"]),
    )


def _a_status(word: str) -> MemberStatus:
    """The column's word as the closed type; the table's CHECK already refused anything else."""
    statuses: dict[str, MemberStatus] = {
        "invited": "invited",
        "active": "active",
        "disabled": "disabled",
    }
    return statuses[word]
