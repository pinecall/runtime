"""The members table: who the people of an org are, their invitations, and what login reads."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pinecall.auth.invitations import Invited
from pinecall.log.store import Pool
from pinecall.types import Member, MemberStatus, Role, a_role

# The row's own name, not a secret: it is what a key's `subject` carries and what a seat says.
MEMBER_ID_PREFIX = "m_"
MEMBER_ID_BYTES = 6


# An address is one person however it is typed: `JP@Cloudacio.com ` and `jp@cloudacio.com` are the
# same login. Every row is written and read through this, so the column only ever holds the one.
def an_address(email: str) -> str:
    """The email as rows keep it: trimmed and lower-cased."""
    return email.strip().lower()


@dataclass(frozen=True)
class Kept:
    """What the login door reads about a person: who they are, and the hash their password left."""

    member: Member
    password_hash: str | None


class Members(Protocol):
    """Where the org's doors invite, list and change its people, and where login finds one."""

    # A PERSON is their email, on this box, and has one password. An org is a row of theirs; a
    # second org is a second row with the same hash, and the hash changes everywhere at once.
    # No migration carried this: 0014's schema already holds a hash per row, and the rule is
    # kept here, in the four verbs that read and write it (2026-09-16).
    async def invite(
        self, org: str, email: str, name: str, role: Role, agents: Iterable[str]
    ) -> Invited | None:
        """A new member with a one-use token, or a fresh token for one still invited. An email
        that already has a password on this box is seated ACTIVE with it, and no token is made.
        None when the email already belongs to a member of THIS org who accepted."""
        ...

    async def accept(self, token: str, password_hash: str) -> Member | None:
        """The invitation spent and the member active with this password, which becomes the
        person's password in every org of theirs. None when no open, unexpired invitation
        answers to the token."""
        ...

    async def a_persons_password(self, email: str) -> str | None:
        """The hash this person's password left, whichever org it was chosen in; None when the
        email has never accepted anywhere."""
        ...

    async def orgs_of(self, email: str) -> tuple[Member, ...]:
        """Every row of this email across the orgs, oldest first: the person's memberships."""
        ...

    async def join(self, org: str, id: str, password_hash: str) -> Member | None:
        """A member still invited seated active with the person's password: a row made before
        the person existed, caught up at their login. None when no invited member answers."""
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

    # A forgotten password, where the box sends no email: the admin is handed a one-use link the
    # way an invitation is handed, and the person opening it chooses a new password through the
    # very door an invitation is accepted at. Only an ACTIVE member is reset; the newest link is
    # the only link, and a member disabled after it was issued is not re-activated by it.
    async def reset(self, org: str, id: str) -> Invited | None:
        """A one-use link that sets this active member's password. None when not active here."""
        ...

    # Not part of `update`, on purpose: everything there is the ORG's to change, on a key with
    # `team`. This one is the BOX's, on the ops key, and putting it in the same call would be one
    # body away from an org promoting its own admin to run the machine it is a tenant on.
    async def make_operator(self, org: str, id: str, operator: bool) -> Member | None:
        """This person runs the box, or stops. None when no member of this org answers to the id."""
        ...


def a_member_id() -> str:
    """A name for the row. It is what a person's key carries as `subject`."""
    return f"{MEMBER_ID_PREFIX}{secrets.token_hex(MEMBER_ID_BYTES)}"


def members_for(pool: Pool | None) -> Members:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    # Imported here and not above: both stores import this module for the protocol and the
    # helpers, and the one place that picks between them is the one place the cycle would close.
    from pinecall.auth.members_memory import MemoryMembers
    from pinecall.auth.members_postgres import PostgresMembers

    return MemoryMembers() if pool is None else PostgresMembers(pool)


def an_instant(seconds: float) -> str:
    """A moment as Postgres hands its timestamps back: ISO 8601, UTC."""
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def text_or_none(column: Any) -> str | None:
    """A nullable text column: the string, or None when the row has none."""
    return None if column is None else str(column)


def a_member_of_row(row: Any) -> Member:
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
