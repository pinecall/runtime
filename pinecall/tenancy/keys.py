"""API keys: minted, revoked, found by fingerprint, and the world and scope a request acts in."""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotFound, NotSignedIn
from pinecall.domain.names import ENVS, PRODUCTION, SANDBOX, Env, parse_env
from pinecall.domain.person import (
    HOLDING,
    KEY_SCOPES,
    READS_ITS_OWN_CALL,
    ROLE_SCOPES,
    SERVER_SCOPES,
    THE_FLEET,
    THE_TEAM,
    Key,
    KeyScope,
    Member,
    Role,
    RoomScope,
)
from pinecall.domain.scope import THE_ORGS_OWN, Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy.people import fingerprint, member_of

# The prefix says the world, so a key pasted into the wrong one is caught before it knocks. A
# person's one key is production's: it opens the sandbox too, and the request names the world.
PRODUCTION_PREFIX = "pc_live_"


SANDBOX_PREFIX = "pc_test_"


KEY_BYTES = 32


ID_PREFIX = "k_"


ID_BYTES = 8


NOT_A_WORLD = "pinecall-env is sandbox or production, not {asked!r}"


ONE_WORLD = (
    "this key is a {world} server's token, and this request is for {asked}: "
    "a server's token opens the world it was made in"
)


NO_PRODUCTION = "{name} has no production access: an admin gives it in Team"


NOT_OPENED = "this key does not open {scope}: it opens {opens}"


EXPIRED = "this key expired at {at}: it opens nothing now, and a new one is made in the console"


NOT_A_SERVERS = "a server's token opens some of {scopes}, and at least one; not {wanted}"


ALREADY_OVER = "a key's expiry is a moment to come, not {at}"


NOT_YOURS_TO_GRANT = (
    "this key does not open everything {role} would: it opens {opens}, so it cannot grant {role}"
)


NOT_THEIR_AGENT = (
    "{name} works on {agents}, and agent {slug} is not one of them: an admin adds it in Team"
)


NOT_YOURS_TO_SWITCH = "{name} has no production access, and cannot give it: an admin does"


CANNOT_LOOK_THERE = "only a key that sees every corner opens a colleague's, and only in the sandbox"


NOT_A_COLLEAGUE = "no active member of this org answers to that corner"


NO_DISPATCH = "the fleet's key acts in the corner of the call it serves: name the call"


NOT_THE_CALLS = "the call named is not in the scope the dispatch names: a worker acts in its call's"


NOT_THIS_FLEET = (
    "this is the {world} fleet's key, and the call is the {asked}'s: the unit holds the wrong key"
)


# A key that names a person opens nothing once the person is gone, disabled, or (on a visit to
# another org) no longer runs the box: the member row is read with the key, every request. Its
# use is written at most once a minute, so a request is not a write. An expired key is found, so
# the refusal can say it expired; it opens nothing and its use is not written.
VERIFY = sql.SQL("""
WITH found AS (
    SELECT id, org, label, env, scopes, subject, name, expires_at, last_used_at,
           expires_at IS NOT NULL AND expires_at <= now() AS expired
    FROM api_keys
    WHERE hash = %(hash)s AND revoked_at IS NULL
), touched AS (
    UPDATE api_keys SET last_used_at = now() FROM found
    WHERE api_keys.id = found.id AND NOT found.expired
      AND (found.last_used_at IS NULL OR found.last_used_at < now() - interval '1 minute')
)
SELECT found.*, m.id AS member, {member}
FROM found LEFT JOIN members m ON m.id = found.subject
WHERE found.subject IS NULL
   OR (m.status = 'active' AND (m.org = found.org OR m.operator))
""").format(
    member=sql.SQL(", ").join(
        sql.SQL("m.{} AS {}").format(sql.Identifier(column), sql.Identifier(f"m_{column}"))
        for column in (
            "org",
            "email",
            "name",
            "role",
            "agents",
            "status",
            "operator",
            "production",
            "verified_at",
        )
    )
)


ISSUE = """
INSERT INTO api_keys (id, hash, org, label, env, scopes, subject, name, created_by, expires_at)
VALUES (%(id)s, %(hash)s, %(org)s, %(label)s, %(env)s, %(scopes)s, %(subject)s, %(name)s,
        %(created_by)s, %(expires_at)s)
"""


LISTED = """
SELECT id, hash, org, label, env, scopes, subject, name, created_by, created_at, last_used_at,
       revoked_at, expires_at
FROM api_keys WHERE org = %(org)s ORDER BY created_at, id
"""


# A second revoke changes nothing and says so.
REVOKE = """
UPDATE api_keys SET revoked_at = now() WHERE hash = %(hash)s AND revoked_at IS NULL RETURNING id
"""


# Room tokens. A room token is livekit's own JWT: the room is the call, our scope rides an
# attribute the worker reads without a lookup, and the same string joins the room and reads
# the log.
SUBJECT_ATTRIBUTE = "pinecall.subject"


NAME_ATTRIBUTE = "pinecall.name"


PROJECTION_ATTRIBUTE = "pinecall.projection"


CODE_ATTRIBUTE = "pinecall.code"


AGENT_ATTRIBUTE = "pinecall.agent"


ENV_ATTRIBUTE = "pinecall.env"


LONGEST_VISIT_TTL_S = 600


A_SEAT_LASTS_S = 15 * 60


A_VISITOR = "web_"


A_SEAT = "sup_"


# The one source a visitor may publish; livekit refuses a source not listed.
THE_MICROPHONE = "microphone"


# An API key holds no dots, so its shape alone tells it from a token.
JWT_PARTS = 3


MINTED = """
INSERT INTO tokens (call, org, env, agent, scope, expires_at)
VALUES (%(call)s, %(org)s, %(env)s, %(agent)s, %(scope)s, to_timestamp(%(expires_at)s))
"""


# Valid for one call: the browser's scopes, and the one that sends verbs.
BOUND_TO_ONE_CALL: frozenset[RoomScope] = READS_ITS_OWN_CALL | frozenset[RoomScope]({"supervise"})


@dataclass(frozen=True)
class Issued:
    """What a new key is made with."""

    org: str
    env: Env
    scopes: frozenset[KeyScope] = KEY_SCOPES
    label: str | None = None
    subject: str | None = None
    name: str | None = None
    created_by: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Bearer:
    """A verified key, and the person it was minted for, read in the same query."""

    key: Key
    member: Member | None = None


@dataclass(frozen=True)
class ListedKey:
    """A key as the org's listing shows it: by fingerprint, never the key."""

    fingerprint: str
    key: Key
    created_by: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


async def issue(pool: Pool, issued: Issued) -> tuple[Key, str]:
    """Mint a key: the row keeps its fingerprint, and the key is handed back this once."""
    prefix = PRODUCTION_PREFIX if issued.env == PRODUCTION else SANDBOX_PREFIX
    secret = f"{prefix}{secrets.token_urlsafe(KEY_BYTES)}"
    key = Key(
        key_id=f"{ID_PREFIX}{secrets.token_hex(ID_BYTES)}",
        org=issued.org,
        label=issued.label,
        env=issued.env,
        scopes=issued.scopes,
        subject=issued.subject,
        name=issued.name,
        expires_at=issued.expires_at,
    )
    values = {
        "id": key.key_id,
        "hash": fingerprint(secret),
        "org": key.org,
        "label": key.label,
        "env": key.env,
        "scopes": sorted(key.scopes),
        "subject": key.subject,
        "name": key.name,
        "created_by": issued.created_by,
        "expires_at": key.expires_at,
    }
    async with pool.connection() as connection:
        await connection.execute(ISSUE, values)
    return key, secret


async def person_key(
    pool: Pool, member: Member, *, parent: Key | None = None, label: str | None = None
) -> tuple[Key, str]:
    """A person's one key: the role's scopes, both worlds, never outliving the key it came from."""
    issued = Issued(
        org=member.org,
        env=PRODUCTION,
        scopes=member.scopes,
        label=label,
        subject=member.id,
        name=member.name,
        created_by=member.id,
        expires_at=None if parent is None else parent.expires_at,
    )
    return await issue(pool, issued)


async def verify(pool: Pool, bearer: str) -> Bearer | None:
    """The key and its person; None for a key unknown, revoked, or whose person is gone."""
    async with pool.connection() as connection:
        row = await (await connection.execute(VERIFY, {"hash": fingerprint(bearer)})).fetchone()
    if row is None:
        return None
    if row["expired"]:
        raise NotSignedIn(EXPIRED.format(at=row["expires_at"].isoformat(timespec="seconds")))
    member = None if row["member"] is None else member_of(_member_columns(row))
    return Bearer(key=_key(row), member=member)


async def listed(pool: Pool, org: str) -> list[ListedKey]:
    """Every key of the org, oldest first, the revoked ones too."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED, {"org": org})).fetchall()
    return [
        ListedKey(
            fingerprint=row["hash"],
            key=_key(row),
            created_by=row["created_by"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            revoked_at=row["revoked_at"],
        )
        for row in rows
    ]


async def revoke(pool: Pool, key_fingerprint: str) -> bool:
    """Stop a key from the next request on; whether a live one was stopped. Its row stays."""
    async with pool.connection() as connection:
        revoked = await connection.execute(REVOKE, {"hash": key_fingerprint})
        return await revoked.fetchone() is not None


def world_of(bearer: Bearer, params: str | None) -> Env:
    """The world a request acts in: a server key's own, else the one its header asks for."""
    if params is not None and params not in ENVS:
        raise NotAllowed(NOT_A_WORLD.format(asked=params))
    wanted = None if params is None else parse_env(params)
    if bearer.member is None:
        if wanted is not None and wanted != bearer.key.env:
            raise NotAllowed(ONE_WORLD.format(world=bearer.key.env, asked=wanted))
        return bearer.key.env
    world = SANDBOX if wanted is None else wanted
    if world == PRODUCTION and not bearer.member.opens_production:
        raise NotAllowed(NO_PRODUCTION.format(name=bearer.member.name))
    return world


def scope_of(
    bearer: Bearer,
    world: Env,
    *,
    looking_at: Member | None = None,
    dispatched: Scope | None = None,
    called: Scope | None = None,
) -> Scope:
    """The scope a request acts in: the org's in production, the person's own in the sandbox."""
    if THE_FLEET in bearer.key.scopes:
        return _fleets_scope(bearer, dispatched, called)
    if world == PRODUCTION or bearer.member is None:
        return Scope(bearer.key.org, world, THE_ORGS_OWN)
    if looking_at is None or looking_at.id == bearer.member.id:
        return Scope(bearer.key.org, world, bearer.member.id)
    # Both team and app: a manager holds team alone and does not open developers' sandboxes.
    if not {THE_TEAM, HOLDING} <= bearer.key.scopes:
        raise NotAllowed(CANNOT_LOOK_THERE)
    if looking_at.org != bearer.key.org or looking_at.status != "active":
        raise NotAllowed(NOT_A_COLLEAGUE)
    return Scope(bearer.key.org, world, looking_at.id)


# Fewer scopes than a server's, never others: a token for pushing knowledge alone opens that.
def server_scopes(wanted: Sequence[str] | None) -> frozenset[KeyScope]:
    """The scopes a server's token is made with: all of a server's, or those of them asked for."""
    if wanted is None:
        return SERVER_SCOPES
    scopes = frozenset[KeyScope](scope for scope in SERVER_SCOPES if scope in wanted)
    if not scopes or len(scopes) != len(set(wanted)):
        wanted = ", ".join(sorted(set(wanted))) or "none"
        raise DeclarationRefused(
            NOT_A_SERVERS.format(scopes=", ".join(sorted(SERVER_SCOPES)), wanted=wanted)
        )
    return scopes


def check_expiry(expires_at: datetime | None, now: datetime) -> None:
    """Refuse an expiry that is not a moment to come; None is a key that never expires."""
    if expires_at is not None and expires_at <= now:
        raise DeclarationRefused(ALREADY_OVER.format(at=expires_at.isoformat(timespec="seconds")))


def check_opens(bearer: Bearer, *scopes: KeyScope) -> None:
    """Refuse, naming what the key does open, unless it holds one of the scopes."""
    if any(scope in bearer.key.scopes for scope in scopes):
        return
    opens = " · ".join(sorted(bearer.key.scopes)) or "nothing"
    raise NotAllowed(NOT_OPENED.format(scope=" or ".join(sorted(scopes)), opens=opens))


# A member's agents bind their own org alone: on a visit the member row is the visitor's own
# org's, and names none of the visited org's agents.
def check_agent(bearer: Bearer, slug: str) -> None:
    """Refuse a person whose list of agents leaves this one out; an empty list is every agent."""
    member = bearer.member
    if member is None or not member.agents or member.org != bearer.key.org:
        return
    if slug in member.agents:
        return
    agents = ", ".join(sorted(member.agents))
    raise NotAllowed(NOT_THEIR_AGENT.format(name=member.name, agents=agents, slug=slug))


# A key grants only a role whose scopes it holds, or a manager would make an admin.
def check_may_grant(bearer: Bearer, role: Role | None, *, production: bool) -> None:
    """Refuse a role or production access the key's holder does not have to give."""
    if (
        role is not None
        and bearer.member is not None
        and not ROLE_SCOPES[role] <= bearer.key.scopes
    ):
        opens = " · ".join(sorted(bearer.key.scopes)) or "nothing"
        raise NotAllowed(NOT_YOURS_TO_GRANT.format(role=role, opens=opens))
    acts_there = (
        bearer.key.env == PRODUCTION if bearer.member is None else bearer.member.opens_production
    )
    if production and not acts_there:
        name = bearer.key.name or "this key"
        raise NotAllowed(NOT_YOURS_TO_SWITCH.format(name=name))


# The call's head row is the proof; the dispatch's word stands only where no call exists yet.
def _fleets_scope(bearer: Bearer, dispatched: Scope | None, called: Scope | None) -> Scope:
    acting = called if called is not None else dispatched
    if acting is None:
        raise DeclarationRefused(NO_DISPATCH)
    if dispatched is not None and dispatched != acting:
        raise NotFound(NOT_THE_CALLS)
    # A fleet serves one world: a unit holding the other world's key is refused here too.
    if acting.env != bearer.key.env:
        raise NotAllowed(NOT_THIS_FLEET.format(world=bearer.key.env, asked=acting.env))
    return acting


def _key(row: DictRow) -> Key:
    return Key(
        key_id=row["id"],
        org=row["org"],
        label=row["label"],
        env=row["env"],
        scopes=frozenset(row["scopes"]),
        subject=row["subject"],
        name=row["name"],
        expires_at=row["expires_at"],
    )


def _member_columns(row: DictRow) -> DictRow:
    columns = {
        name.removeprefix("m_"): value for name, value in row.items() if name.startswith("m_")
    }
    return {**columns, "id": row["member"]}
