"""The members table in this process's memory: a clone on a dev key, and every unit test."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from pinecall.auth.invitations import INVITATION_TTL_S, Invited, a_token
from pinecall.auth.keys import fingerprint
from pinecall.auth.members import Kept, a_member_id, an_address, an_instant
from pinecall.types import Member, MemberStatus, Role


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
    # Whether accepting it proves the address: it travelled by mail alone, or the box issued it.
    vouched: bool = False


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
            member.id: _Row(member, None, an_instant(clock())) for member in rows
        }
        self._invitations: dict[str, _Invitation] = {}

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
        """One row per (org, email); a second invite of one still invited replaces the token."""
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
            # Seated with the password they have only when the address is PROVED theirs: a
            # password somebody chose through a handed link says nothing about who chose it.
            if known is not None and await self.verified(email):
                member = replace(member, status="active", verified=True)
                self._rows[member.id] = _Row(member, known, an_instant(self._clock()))
                return Invited(member=member, token=None, expires_at=None)
            self._rows[member.id] = _Row(member, None, an_instant(self._clock()))
        else:
            member = kept.member
            self._spend_every_open_link_of(member.id)
        return self._a_link_for(member, vouched)

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """Spend the token, then make the member active with this password, everywhere."""
        hashed = fingerprint(token)
        invitation = self._invitations.get(hashed)
        if invitation is None or invitation.spent or invitation.expires_at <= self._clock():
            return None
        self._invitations[hashed] = replace(invitation, spent=True)
        row = self._rows[invitation.member]
        if row.member.status == "disabled":
            return None
        member = replace(
            row.member, status="active", verified=row.member.verified or invitation.vouched
        )
        self._rows[member.id] = replace(row, member=member, password_hash=password_hash)
        self._password_everywhere(member.email, password_hash)
        return member

    async def a_persons_password(self, email: str) -> str | None:
        """The newest hash any row of this email holds."""
        email = an_address(email)
        rows = [row for row in self._rows.values() if row.member.email == email]
        for row in sorted(rows, key=lambda row: row.created_at, reverse=True):
            if row.password_hash is not None:
                return row.password_hash
        return None

    async def orgs_of(self, email: str) -> tuple[Member, ...]:
        """Every row of this email, in the order they were made."""
        email = an_address(email)
        return tuple(row.member for row in self._rows.values() if row.member.email == email)

    async def verified(self, email: str) -> bool:
        """Whether any row of this address was proved to be this person's."""
        return any(row.verified for row in await self.orgs_of(email))

    async def join(self, org: str, id: str, password_hash: str) -> Member | None:
        """The invited row seated active with the password the person already has."""
        found = await self.find(org, id)
        if found is None or found.status != "invited":
            return None
        member = replace(found, status="active")
        self._rows[id] = replace(self._rows[id], member=member, password_hash=password_hash)
        return member

    async def vouched_for(self, org: str, id: str) -> Member | None:
        """Active and verified on a provider's word; a disabled member stays disabled."""
        found = await self.find(org, id)
        if found is None or found.status == "disabled":
            return None
        member = replace(found, status="active", verified=True)
        self._rows[id] = replace(self._rows[id], member=member)
        return member

    def _password_everywhere(self, email: str, password_hash: str) -> None:
        """One person, one password: every row of theirs takes the hash just chosen."""
        for id, row in self._rows.items():
            if row.member.email == email and row.password_hash is not None:
                self._rows[id] = replace(row, password_hash=password_hash)

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
        email = an_address(email)
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
        production: bool | None = None,
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
            production=found.production if production is None else production,
        )
        self._rows[id] = replace(self._rows[id], member=member)
        return member

    async def reset(self, org: str, id: str, *, vouched: bool = False) -> Invited | None:
        """Every open link of theirs spent, and a new one, for a member who is active."""
        found = await self.find(org, id)
        if found is None or found.status != "active":
            return None
        self._spend_every_open_link_of(id)
        return self._a_link_for(found, vouched)

    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """The same one column, over a dict."""
        found = await self.find(org, id)
        if found is None:
            return None
        member = replace(found, operator=operator)
        self._rows[id] = replace(self._rows[id], member=member)
        return member

    async def remove(self, org: str, id: str) -> bool:
        """The row out of the dict, and every link that named it: what the CASCADE does below."""
        if await self.find(org, id) is None:
            return False
        del self._rows[id]
        self._invitations = {
            hashed: invitation
            for hashed, invitation in self._invitations.items()
            if invitation.member != id
        }
        return True

    def _spend_every_open_link_of(self, member: str) -> None:
        """The newest link is the only link: a re-invite or a reset spends the ones before it."""
        for hashed, invitation in self._invitations.items():
            if invitation.member == member and not invitation.spent:
                self._invitations[hashed] = replace(invitation, spent=True)

    def _a_link_for(self, member: Member, vouched: bool) -> Invited:
        """One token for this member, good for a week, remembered by its fingerprint."""
        token, expires_at = a_token(), self._clock() + INVITATION_TTL_S
        self._invitations[fingerprint(token)] = _Invitation(member.id, expires_at, vouched=vouched)
        return Invited(member=member, token=token, expires_at=an_instant(expires_at))
