"""People: an org's members, their invitations and one password each, and the login throttle."""

import asyncio
import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound, QuotaExhausted
from pinecall.domain.types import Member, MemberStatus, Role
from pinecall.postgres.pool import Connection, Pool

MEMBER_PREFIX = "m_"
MEMBER_BYTES = 6
# Stored as sha256 like a key, but it dies in a week: a mailed link gets forwarded.
INVITATION_PREFIX = "inv_"
INVITATION_BYTES = 24
INVITATION_TTL_S = 7 * 24 * 3600

TOO_SHORT = "a password is at least {shortest} characters"
ACCEPTED_ALREADY = "{email} is a member of this org already: they sign in, nobody invites them"
NO_SEAT_LEFT = "the org holds all {seated} of its seats: free one, or ask for more"
LAST_ADMIN = "the org keeps one active admin: make another one admin first"
NOBODY_BY_THAT_ID = "nobody by that id in this org"

# Its own statement, so the insert after it sees what a concurrent invite wrote; inside one
# statement the seat count raced.
ROW = sql.SQL("id, org, email, name, role, agents, status, operator, production, verified_at")
HELD = "SELECT pg_advisory_xact_lock(hashtext(%(org)s))"
SEATED = "SELECT count(*) AS seated FROM members WHERE org = %(org)s AND status <> 'disabled'"
INSERT = """
INSERT INTO members (id, org, email, name, role, agents, status, password_hash, production,
                     verified_at)
VALUES (%(id)s, %(org)s, %(email)s, %(name)s, %(role)s, %(agents)s, %(status)s, %(password)s,
        %(production)s, CASE WHEN %(verified)s THEN now() END)
ON CONFLICT (org, email) DO NOTHING
RETURNING id
"""
LISTED = sql.SQL("SELECT {row} FROM members WHERE org = %(org)s ORDER BY created_at, id").format(
    row=ROW
)
FIND = sql.SQL("SELECT {row} FROM members WHERE org = %(org)s AND id = %(id)s").format(row=ROW)
BY_EMAIL = sql.SQL("SELECT {row} FROM members WHERE org = %(org)s AND email = %(email)s").format(
    row=ROW
)
ORGS_OF = sql.SQL(
    "SELECT {row} FROM members WHERE email = %(email)s ORDER BY created_at, id"
).format(row=ROW)
# One password per person: the newest row that has one speaks for every row of the address.
HASH_OF = """
SELECT password_hash FROM members WHERE email = %(email)s AND password_hash IS NOT NULL
ORDER BY created_at DESC LIMIT 1
"""
VERIFIED = "SELECT 1 FROM members WHERE email = %(email)s AND verified_at IS NOT NULL LIMIT 1"
# A NULL parameter keeps what the column holds.
UPDATE = sql.SQL("""
UPDATE members SET role = COALESCE(%(role)s, role), agents = COALESCE(%(agents)s, agents),
                   status = COALESCE(%(status)s, status),
                   production = COALESCE(%(production)s, production)
WHERE org = %(org)s AND id = %(id)s
RETURNING {row}
""").format(row=ROW)
# Never wakes a disabled member; a vouched link also proves the address.
ACTIVATE = sql.SQL("""
UPDATE members SET status = 'active', password_hash = %(password)s,
       verified_at = CASE WHEN %(vouched)s THEN COALESCE(verified_at, now()) ELSE verified_at END
WHERE id = %(id)s AND status <> 'disabled'
RETURNING {row}
""").format(row=ROW)
HASH_EVERYWHERE = """
UPDATE members SET password_hash = %(password)s
WHERE email = %(email)s AND password_hash IS NOT NULL
"""
JOIN = sql.SQL("""
UPDATE members SET status = 'active', password_hash = %(password)s
WHERE org = %(org)s AND id = %(id)s AND status = 'invited'
RETURNING {row}
""").format(row=ROW)
VOUCHED = sql.SQL("""
UPDATE members SET status = 'active', verified_at = COALESCE(verified_at, now())
WHERE org = %(org)s AND id = %(id)s AND status <> 'disabled'
RETURNING {row}
""").format(row=ROW)
OPERATOR = sql.SQL(
    "UPDATE members SET operator = %(on)s WHERE org = %(org)s AND id = %(id)s RETURNING {row}"
).format(row=ROW)
OTHER_ADMINS = """
SELECT count(*) AS others FROM members
WHERE org = %(org)s AND id <> %(id)s AND role = 'admin' AND status = 'active'
"""
# Revoked, not deleted: a log names the key that wrote it.
REVOKE_THEIR_KEYS = """
UPDATE api_keys SET revoked_at = now() WHERE subject = %(id)s AND revoked_at IS NULL
"""
# Their invitations go by the foreign key's cascade.
REMOVE = "DELETE FROM members WHERE org = %(org)s AND id = %(id)s RETURNING id"
INVITE = """
INSERT INTO invitations (token_hash, member, expires_at, vouched)
VALUES (%(hash)s, %(member)s, %(expires_at)s, %(vouched)s)
"""
SPEND_OPEN = (
    "UPDATE invitations SET spent_at = now() WHERE member = %(member)s AND spent_at IS NULL"
)
# One conditional update, so two accepts of one link cannot both win.
SPEND = """
UPDATE invitations SET spent_at = now()
WHERE token_hash = %(hash)s AND spent_at IS NULL AND expires_at > now()
RETURNING member, vouched
"""

# argon2id with the library's defaults (OWASP's). A key is random and hashed with sha256; a
# password is chosen by a person, so it is hashed slowly.
_HASHER = PasswordHasher()
# Verified when the address is nobody's, so the time taken does not say who is a member.
_NOBODYS = _HASHER.hash(secrets.token_urlsafe(32))

# Every attempt counts, not only the misses: counting misses would say which guess was right.
TRIES = 5
WINDOW_S = 60.0
# Names are the attacker's to choose, so quiet ones are swept past this many.
SWEEP_AT = 1024


@dataclass(frozen=True)
class Invitee:
    """Who is invited, and what they are given."""

    email: str
    name: str
    role: Role
    agents: frozenset[str] = frozenset()
    production: bool = False


@dataclass(frozen=True)
class Change:
    """What a PATCH of a member names; None keeps what the member has."""

    role: Role | None = None
    agents: frozenset[str] | None = None
    status: MemberStatus | None = None
    production: bool | None = None


@dataclass(frozen=True)
class Invited:
    """The member invited, and the link's token once; no token when seated at once."""

    member: Member
    token: str | None = None
    expires_at: datetime | None = None


class Throttle:
    """How many times a name knocked in the last minute: five, and the sixth waits."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """A throttle nobody knocked at."""
        self.clock = clock
        self.knocks: dict[str, deque[float]] = {}

    def allowed(self, name: str) -> bool:
        """Count a knock, and say whether it is within the window's tries."""
        now = self.clock()
        if len(self.knocks) >= SWEEP_AT:
            for quiet in [one for one, at in self.knocks.items() if at[-1] <= now - WINDOW_S]:
                del self.knocks[quiet]
        knocks = self.knocks.setdefault(name, deque())
        while knocks and knocks[0] <= now - WINDOW_S:
            knocks.popleft()
        if len(knocks) >= TRIES:
            return False
        knocks.append(now)
        return True


def fingerprint(secret: str) -> str:
    """The sha256 a secret handed out once is kept as: a key, an invitation, a code."""
    return hashlib.sha256(secret.encode()).hexdigest()


def member_of(row: DictRow) -> Member:
    """A member read off its row."""
    return Member(
        id=row["id"],
        org=row["org"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        agents=frozenset(row["agents"]),
        status=row["status"],
        operator=row["operator"],
        production=row["production"],
        verified=row["verified_at"] is not None,
    )


# argon2id costs tens of milliseconds and 64 MiB, so it runs off the event loop.
async def hash_password(password: str, shortest: int) -> str:
    """The password hashed; refused before hashing when it is under the box's floor."""
    if len(password) < shortest:
        raise DeclarationRefused(TOO_SHORT.format(shortest=shortest))
    return await asyncio.to_thread(_HASHER.hash, password)


async def matches(password: str, kept: str | None) -> bool:
    """Whether the password is the one kept; nobody's costs a verification all the same."""
    return await asyncio.to_thread(_verified, password, kept)


async def invite(
    pool: Pool, org: str, invitee: Invitee, *, seats: int | None, vouched: bool = False
) -> Invited:
    """Invite a person, or give a link again to one still invited; within the org's seats."""
    email = _folded(invitee.email)
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(HELD, {"org": org})
        kept = await _one(connection, BY_EMAIL, {"org": org, "email": email})
        if kept is not None and kept.status != "invited":
            raise Conflict(ACCEPTED_ALREADY.format(email=email))
        if kept is not None:
            await connection.execute(SPEND_OPEN, {"member": kept.id})
            return await _link(connection, kept, vouched=vouched)
        seated = await (await connection.execute(SEATED, {"org": org})).fetchone()
        held = 0 if seated is None else int(seated["seated"])
        if seats is not None and held >= seats:
            raise QuotaExhausted(NO_SEAT_LEFT.format(seated=held))
        password = await _password_of(connection, email)
        # A person proven elsewhere, with a password, is seated at once: no link to accept.
        at_once = password is not None and await _verified_anywhere(connection, email)
        member = Member(
            id=f"{MEMBER_PREFIX}{secrets.token_hex(MEMBER_BYTES)}",
            org=org,
            email=email,
            name=invitee.name,
            role=invitee.role,
            agents=invitee.agents,
            status="active" if at_once else "invited",
            production=invitee.production,
            verified=at_once,
        )
        await connection.execute(INSERT, _written(member, password if at_once else None))
        if at_once:
            return Invited(member=member)
        return await _link(connection, member, vouched=vouched)


async def accept(pool: Pool, token: str, hashed: str) -> Member | None:
    """Spend the link and set the password everywhere the person is; None for a dead link."""
    async with pool.connection() as connection, connection.transaction():
        spent = await (await connection.execute(SPEND, {"hash": fingerprint(token)})).fetchone()
        if spent is None:
            return None
        values = {"id": spent["member"], "password": hashed, "vouched": spent["vouched"]}
        member = await _one(connection, ACTIVATE, values)
        if member is not None:
            everywhere = {"email": member.email, "password": hashed}
            await connection.execute(HASH_EVERYWHERE, everywhere)
    return member


async def reset(pool: Pool, org: str, member: str, *, vouched: bool = False) -> Invited | None:
    """A link that sets an active member's password again; None for anybody else."""
    async with pool.connection() as connection, connection.transaction():
        found = await _one(connection, FIND, {"org": org, "id": member})
        if found is None or found.status != "active":
            return None
        await connection.execute(SPEND_OPEN, {"member": member})
        return await _link(connection, found, vouched=vouched)


async def update(pool: Pool, org: str, member: str, change: Change) -> Member:
    """Change what was named of a member; disabling one revokes their keys."""
    agents = None if change.agents is None else sorted(change.agents)
    values = {
        "org": org,
        "id": member,
        "role": change.role,
        "agents": agents,
        "status": change.status,
        "production": change.production,
    }
    async with pool.connection() as connection, connection.transaction():
        updated = await _one(connection, UPDATE, values)
        if updated is None:
            raise NotFound(NOBODY_BY_THAT_ID)
        if change.status == "disabled":
            await connection.execute(REVOKE_THEIR_KEYS, {"id": member})
    return updated


async def remove(pool: Pool, org: str, member: str) -> None:
    """Take a member out for good, their keys revoked first; never the last active admin."""
    async with pool.connection() as connection, connection.transaction():
        found = await _one(connection, FIND, {"org": org, "id": member})
        if found is None:
            raise NotFound(NOBODY_BY_THAT_ID)
        if found.role == "admin" and found.status == "active":
            others = await (
                await connection.execute(OTHER_ADMINS, {"org": org, "id": member})
            ).fetchone()
            if others is None or others["others"] == 0:
                raise Conflict(LAST_ADMIN)
        await connection.execute(REVOKE_THEIR_KEYS, {"id": member})
        await connection.execute(REMOVE, {"org": org, "id": member})


async def listed(pool: Pool, org: str) -> list[Member]:
    """The org's people, oldest first, the disabled ones too."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED, {"org": org})).fetchall()
    return [member_of(row) for row in rows]


async def seated(pool: Pool, org: str) -> int:
    """How many of the org's people hold a seat: every one not disabled."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SEATED, {"org": org})).fetchone()
    return 0 if row is None else int(row["seated"])


async def find(pool: Pool, org: str, member: str) -> Member | None:
    """The member by id, within the org."""
    async with pool.connection() as connection:
        return await _one(connection, FIND, {"org": org, "id": member})


async def by_email(pool: Pool, org: str, email: str) -> Member | None:
    """The member by address, within the org."""
    async with pool.connection() as connection:
        return await _one(connection, BY_EMAIL, {"org": org, "email": _folded(email)})


async def orgs_of(pool: Pool, email: str) -> list[Member]:
    """Every membership of the address, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(ORGS_OF, {"email": _folded(email)})).fetchall()
    return [member_of(row) for row in rows]


async def password_of(pool: Pool, email: str) -> str | None:
    """The person's one password hash, or None when they have not set one."""
    async with pool.connection() as connection:
        return await _password_of(connection, _folded(email))


async def join(pool: Pool, org: str, member: str, password_hash: str) -> Member | None:
    """Seat an invited member who signs in with the password they already have."""
    async with pool.connection() as connection:
        values = {"org": org, "id": member, "password": password_hash}
        return await _one(connection, JOIN, values)


async def vouched(pool: Pool, org: str, member: str) -> Member | None:
    """Seat a member an identity provider vouched for; never a disabled one."""
    async with pool.connection() as connection:
        return await _one(connection, VOUCHED, {"org": org, "id": member})


async def make_operator(pool: Pool, org: str, member: str, *, on: bool) -> Member | None:
    """Make a member one who runs the box, or take it back at once."""
    async with pool.connection() as connection:
        return await _one(connection, OPERATOR, {"org": org, "id": member, "on": on})


def _verified(password: str, kept: str | None) -> bool:
    try:
        return _HASHER.verify(_NOBODYS if kept is None else kept, password) and kept is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def _link(connection: Connection, member: Member, *, vouched: bool) -> Invited:
    token = f"{INVITATION_PREFIX}{secrets.token_urlsafe(INVITATION_BYTES)}"
    expires_at = datetime.fromtimestamp(time.time() + INVITATION_TTL_S, UTC)
    values = {
        "hash": fingerprint(token),
        "member": member.id,
        "expires_at": expires_at,
        "vouched": vouched,
    }
    await connection.execute(INVITE, values)
    return Invited(member=member, token=token, expires_at=expires_at)


async def _one(
    connection: Connection, query: sql.Composed, values: Mapping[str, object]
) -> Member | None:
    row = await (await connection.execute(query, values)).fetchone()
    return None if row is None else member_of(row)


async def _password_of(connection: Connection, email: str) -> str | None:
    row = await (await connection.execute(HASH_OF, {"email": email})).fetchone()
    return None if row is None else row["password_hash"]


async def _verified_anywhere(connection: Connection, email: str) -> bool:
    return await (await connection.execute(VERIFIED, {"email": email})).fetchone() is not None


def _written(member: Member, password: str | None) -> dict[str, object]:
    return {
        "id": member.id,
        "org": member.org,
        "email": member.email,
        "name": member.name,
        "role": member.role,
        "agents": sorted(member.agents),
        "status": member.status,
        "password": password,
        "production": member.production,
        "verified": member.verified,
    }


def _folded(email: str) -> str:
    return email.strip().lower()
