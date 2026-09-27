"""Keys: the ones that open the gateway, the world and corner a request acts in, and room tokens."""

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import jwt
from livekit.api import AccessToken, VideoGrants
from livekit.protocol.room import RoomConfiguration
from psycopg import sql
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pinecall.domain.errors import DeclarationRefused, NotAllowed
from pinecall.domain.types import (
    ENVS,
    HOLDING,
    KEY_SCOPES,
    PRODUCTION,
    READS_ITS_OWN_CALL,
    ROLE_SCOPES,
    SANDBOX,
    SCOPE_ATTRIBUTE,
    THE_FLEET,
    THE_ORGS_OWN,
    THE_TEAM,
    Corner,
    Env,
    Key,
    KeyScope,
    Member,
    Role,
    RoomScope,
    parse_env,
)
from pinecall.postgres.pool import Pool
from pinecall.tenancy.people import fingerprint, member_of
from pinecall.wire.parts import Projection

# The prefix says the world, so a key pasted into the wrong one is caught before it knocks. A
# person's one key is production's: it opens the sandbox too, and the request names the world.
PRODUCTION_PREFIX = "pc_live_"
SANDBOX_PREFIX = "pc_test_"
KEY_BYTES = 32
ID_PREFIX = "k_"
ID_BYTES = 8

# The world a person's key acts in, as the CLI's --prod and the console's switch send it.
WORLD_HEADER = "pinecall-env"

NOT_A_WORLD = "pinecall-env is sandbox or production, not {asked!r}"
ONE_WORLD = (
    "this key is a {world} server's token, and this request is for {asked}: "
    "a server's token opens the world it was made in"
)
NO_PRODUCTION = "{name} has no production access: an admin gives it in Team"
NOT_OPENED = "this key does not open {scope}: it opens {opens}"
NOT_YOURS_TO_GRANT = (
    "this key does not open everything {role} would: it opens {opens}, so it cannot grant {role}"
)
NOT_YOURS_TO_SWITCH = "{name} has no production access, and cannot give it: an admin does"
CANNOT_LOOK_THERE = "only a key that sees every corner opens a colleague's, and only in the sandbox"
NOT_A_COLLEAGUE = "no active member of this org answers to that corner"
NO_DISPATCH = "the fleet's key acts in the corner of the call it serves: name the call"
NOT_THIS_FLEET = (
    "this is the {world} fleet's key, and the call is the {asked}'s: the unit holds the wrong key"
)

# A key that names a person opens nothing once the person is gone, disabled, or (on a visit to
# another org) no longer runs the box: the member row is read with the key, every request. Its
# use is written at most once a minute, so a request is not a write.
VERIFY = sql.SQL("""
WITH found AS (
    SELECT id, org, label, env, scopes, subject, name, expires_at, last_used_at FROM api_keys
    WHERE hash = %(hash)s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
), touched AS (
    UPDATE api_keys SET last_used_at = now() FROM found
    WHERE api_keys.id = found.id
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
ONE_VISIT_TTL_S = 60
LONGEST_VISIT_TTL_S = 600
# Longer than any call, short enough that a copied link dies the same day.
READ_TTL_S = 4 * 60 * 60
A_SEAT_LASTS_S = 15 * 60
A_VISITOR = "web_"
A_SEAT = "sup_"
IDENTITY_BYTES = 6
# The one source a visitor may publish; livekit refuses a source not listed.
THE_MICROPHONE = "microphone"
# An API key holds no dots, so its shape alone tells it from a token.
JWT_PARTS = 3

MINTED = """
INSERT INTO tokens (call, org, agent, scope, expires_at)
VALUES (%(call)s, %(org)s, %(agent)s, %(scope)s, to_timestamp(%(expires_at)s))
"""
# The condition makes the spend atomic: of two dispatches racing, one updates the row.
SPEND = (
    "UPDATE tokens SET spent_at = now() WHERE call = %(call)s AND spent_at IS NULL RETURNING call"
)
KNOWN = "SELECT 1 FROM tokens WHERE call = %(call)s"

type Spending = Literal["spent", "already_spent", "never_minted"]


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
class Listed:
    """A key as the org's listing shows it: by fingerprint, never the key."""

    fingerprint: str
    key: Key
    created_by: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class Signer:
    """The LiveKit key pair a room token is signed and checked with."""

    api_key: str
    secret: str


@dataclass(frozen=True, kw_only=True)
class Grant:
    """What a room token of one scope may do."""

    connects: bool
    audio: bool
    reads_log: bool
    sends_verbs: bool
    own_call_only: bool
    ttl_s: int | None
    # `audio` publishes and subscribes; `hears` only subscribes; `hidden` is not seen in the room.
    hears: bool = False
    hidden: bool = False


GRANTS: Mapping[RoomScope, Grant] = {
    # It reads its own call's log too, so a browser holds one token.
    "talk": Grant(
        connects=True,
        audio=True,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        ttl_s=ONE_VISIT_TTL_S,
    ),
    # It subscribes: livekit delivers its text streams to subscribers only.
    "chat": Grant(
        connects=True,
        audio=False,
        hears=True,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        ttl_s=ONE_VISIT_TTL_S,
    ),
    "observe": Grant(
        connects=False,
        audio=False,
        hears=True,
        hidden=True,
        reads_log=True,
        sends_verbs=False,
        own_call_only=False,
        ttl_s=None,
    ),
    # Not hidden: livekit delivers no track of a hidden seat, so a takeover would be silent.
    "supervise": Grant(
        connects=False,
        audio=True,
        reads_log=True,
        sends_verbs=True,
        own_call_only=False,
        ttl_s=None,
    ),
    "read": Grant(
        connects=False,
        audio=False,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        ttl_s=READ_TTL_S,
    ),
    "participate": Grant(
        connects=False,
        audio=False,
        reads_log=True,
        sends_verbs=False,
        own_call_only=True,
        ttl_s=None,
    ),
}
# Valid for one call: the browser's scopes, and the one that sends verbs.
BOUND_TO_ONE_CALL: frozenset[RoomScope] = READS_ITS_OWN_CALL | frozenset[RoomScope]({"supervise"})
# What POST /v1/tokens may mint: the scopes that join a room.
MINTED_FOR_A_VISIT: frozenset[RoomScope] = frozenset(
    scope for scope, grant in GRANTS.items() if grant.connects
)
# From the credential, never from a parameter: a scope that reads beyond its own call is the
# tenant's; an API key is the tenant.
PROJECTION_OF: Mapping[RoomScope, Projection] = {
    scope: "tenant" if grant.reads_log and not grant.own_call_only else "public"
    for scope, grant in GRANTS.items()
}


@dataclass(frozen=True)
class Visitor:
    """Who a room token seats, until when, and the dispatch it carries."""

    expires_at: float
    identity: str | None = None
    subject: str | None = None
    name: str | None = None
    # Signed inside the token, so the browser cannot change the agent, org or world it reaches.
    dispatch: RoomConfiguration | None = None


@dataclass(frozen=True)
class Visit:
    """What a checked room token says."""

    call: str
    scope: RoomScope
    expires_at: float
    identity: str | None = None
    subject: str | None = None
    name: str | None = None
    projection: Projection | None = None
    # A code token names its code, agent and world instead of a call; `call` is "" for it.
    code: str | None = None
    agent: str | None = None
    env: Env | None = None


class _Room(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    room: str = ""


class _Said(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    scope: RoomScope = Field(alias=SCOPE_ATTRIBUTE)
    subject: str | None = Field(None, alias=SUBJECT_ATTRIBUTE)
    name: str | None = Field(None, alias=NAME_ATTRIBUTE)
    projection: Projection | None = Field(None, alias=PROJECTION_ATTRIBUTE)
    code: str | None = Field(None, alias=CODE_ATTRIBUTE)
    agent: str | None = Field(None, alias=AGENT_ATTRIBUTE)
    env: Env | None = Field(None, alias=ENV_ATTRIBUTE)


# livekit's claims, the ones a token of ours carries; its verifier drops `exp`.
class _Claims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    exp: float
    sub: str | None = None
    video: _Room = _Room()
    attributes: _Said


@dataclass(frozen=True)
class Seat:
    """A person's seat in a live call: the token and who it says they are."""

    call: str
    identity: str
    token: str
    subject: str | None
    name: str | None


@dataclass(frozen=True)
class Minted:
    """A room token as the ledger keeps it: one call, spent once."""

    call: str
    org: str
    agent: str
    scope: RoomScope
    expires_at: float


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


async def person_key(pool: Pool, member: Member, *, parent: Key | None = None) -> tuple[Key, str]:
    """A person's one key: the role's scopes, both worlds, never outliving the key it came from."""
    issued = Issued(
        org=member.org,
        env=PRODUCTION,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
        created_by=member.id,
        expires_at=None if parent is None else parent.expires_at,
    )
    return await issue(pool, issued)


async def verify(pool: Pool, bearer: str) -> Bearer | None:
    """The key and its person; None for a key unknown, revoked, expired, or whose person is gone."""
    async with pool.connection() as connection:
        row = await (await connection.execute(VERIFY, {"hash": fingerprint(bearer)})).fetchone()
    if row is None:
        return None
    member = None if row["member"] is None else member_of(_member_columns(row))
    return Bearer(key=_key(row), member=member)


async def listed(pool: Pool, org: str) -> list[Listed]:
    """Every key of the org, oldest first, the revoked ones too."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED, {"org": org})).fetchall()
    return [
        Listed(
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


def world_of(bearer: Bearer, asked: str | None) -> Env:
    """The world a request acts in: a server key's own; a person's, the one asked or the sandbox."""
    if asked is not None and asked not in ENVS:
        raise NotAllowed(NOT_A_WORLD.format(asked=asked))
    if bearer.member is None:
        if asked is not None and asked != bearer.key.env:
            raise NotAllowed(ONE_WORLD.format(world=bearer.key.env, asked=asked))
        return bearer.key.env
    world = SANDBOX if asked is None else parse_env(asked)
    if world == PRODUCTION and not bearer.member.opens_production:
        raise NotAllowed(NO_PRODUCTION.format(name=bearer.member.name))
    return world


def corner_of(
    bearer: Bearer,
    world: Env,
    *,
    looking_at: Member | None = None,
    dispatched: Corner | None = None,
) -> Corner:
    """The corner a request acts in: the org's in production, the person's own in the sandbox."""
    if THE_FLEET in bearer.key.scopes:
        if dispatched is None:
            raise DeclarationRefused(NO_DISPATCH)
        # A fleet serves one world: a unit holding the other world's key is refused here too.
        if dispatched.env != bearer.key.env:
            raise NotAllowed(NOT_THIS_FLEET.format(world=bearer.key.env, asked=dispatched.env))
        return dispatched
    if world == PRODUCTION or bearer.member is None:
        return Corner(bearer.key.org, world, THE_ORGS_OWN)
    if looking_at is None or looking_at.id == bearer.member.id:
        return Corner(bearer.key.org, world, bearer.member.id)
    # Both team and app: a manager holds team alone and does not open developers' sandboxes.
    if not {THE_TEAM, HOLDING} <= bearer.key.scopes:
        raise NotAllowed(CANNOT_LOOK_THERE)
    if looking_at.org != bearer.key.org or looking_at.status != "active":
        raise NotAllowed(NOT_A_COLLEAGUE)
    return Corner(bearer.key.org, world, looking_at.id)


def check_opens(bearer: Bearer, *scopes: KeyScope) -> None:
    """Refuse, naming what the key does open, unless it holds one of the scopes."""
    if any(scope in bearer.key.scopes for scope in scopes):
        return
    opens = " · ".join(sorted(bearer.key.scopes)) or "nothing"
    raise NotAllowed(NOT_OPENED.format(scope=" or ".join(sorted(scopes)), opens=opens))


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


def is_a_jwt(bearer: str) -> bool:
    """Whether the bearer is shaped like a token, not a key: its shape, not its signature."""
    parts = bearer.split(".")
    return len(parts) == JWT_PARTS and all(parts)


def room_token(signer: Signer, call: str, scope: RoomScope, visitor: Visitor) -> str:
    """A token that seats the visitor in the call's room with the scope's grants."""
    grant = GRANTS[scope]
    grants = VideoGrants(
        room=call,
        room_join=True,
        can_publish=grant.audio,
        can_subscribe=grant.audio or grant.hears,
        # A widget talks to its call over the data channel, whatever the scope.
        can_publish_data=True,
        can_publish_sources=[THE_MICROPHONE] if grant.audio else [],
        hidden=grant.hidden,
    )
    who = {
        attribute: value
        for attribute, value in (
            (SUBJECT_ATTRIBUTE, visitor.subject),
            (NAME_ATTRIBUTE, visitor.name),
        )
        if value
    }
    token = (
        AccessToken(signer.api_key, signer.secret)
        .with_identity(visitor.identity or _identity(A_VISITOR))
        .with_grants(grants)
        # livekit takes a lifetime and stamps the expiry itself.
        .with_ttl(timedelta(seconds=visitor.expires_at - time.time()))
        .with_attributes({**who, SCOPE_ATTRIBUTE: scope})
    )
    if visitor.dispatch is not None:
        token = token.with_room_config(visitor.dispatch)
    return token.to_jwt()


def seat(signer: Signer, call: str, scope: RoomScope, bearer: Bearer) -> Seat:
    """A seat for the key's person in a live call, to listen or to supervise."""
    identity = _identity(A_SEAT)
    name = bearer.member.name if bearer.member is not None else bearer.key.name
    visitor = Visitor(
        expires_at=time.time() + A_SEAT_LASTS_S,
        identity=identity,
        subject=bearer.key.subject,
        name=name,
    )
    token = room_token(signer, call, scope, visitor)
    return Seat(call=call, identity=identity, token=token, subject=bearer.key.subject, name=name)


# It names the call's room without the right to join it: livekit refuses it, and our doors read
# it as the call's.
def log_token(signer: Signer, call: str, projection: Projection) -> str:
    """A token that reads one call's log and recording, through the projection named."""
    attributes = {SCOPE_ATTRIBUTE: "read", PROJECTION_ATTRIBUTE: projection}
    return _unjoinable(signer, call, READ_TTL_S, attributes)


def code_token(signer: Signer, code: str, agent: str, world: Env, expires_at: float) -> str:
    """A token that asks after one caller code until the code dies; it opens no room."""
    attributes = {
        SCOPE_ATTRIBUTE: "read",
        CODE_ATTRIBUTE: code,
        AGENT_ATTRIBUTE: agent,
        ENV_ATTRIBUTE: world,
    }
    return _unjoinable(signer, f"code:{code}", expires_at - time.time(), attributes)


def read(signer: Signer, token: str) -> Visit | None:
    """What a token of ours says; None for anything else, whatever the reason."""
    try:
        decoded = jwt.decode(
            token, signer.secret, algorithms=["HS256"], options={"require": ["exp"]}, leeway=0
        )
        claims = _Claims.model_validate(decoded)
    except (jwt.PyJWTError, ValidationError):
        return None
    said = claims.attributes
    # A livekit token minted for another purpose with the same pair reads nothing here, and a
    # listener hears the room but reads no log.
    if not claims.video.room or said.scope not in BOUND_TO_ONE_CALL:
        return None
    return Visit(
        call="" if said.code is not None else claims.video.room,
        scope=said.scope,
        expires_at=claims.exp,
        identity=claims.sub,
        subject=said.subject,
        name=said.name,
        projection=said.projection,
        code=said.code,
        agent=said.agent,
        env=said.env,
    )


async def minted(pool: Pool, token: Minted) -> None:
    """Keep a room token the ledger will spend once, when its call opens."""
    values = {
        "call": token.call,
        "org": token.org,
        "agent": token.agent,
        "scope": token.scope,
        "expires_at": token.expires_at,
    }
    async with pool.connection() as connection:
        await connection.execute(MINTED, values)


async def spend(pool: Pool, call: str) -> Spending:
    """Spend the call's token: once, and a second spend is told from a token never minted."""
    async with pool.connection() as connection:
        if await (await connection.execute(SPEND, {"call": call})).fetchone() is not None:
            return "spent"
        known = await (await connection.execute(KNOWN, {"call": call})).fetchone()
    return "never_minted" if known is None else "already_spent"


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


def _unjoinable(signer: Signer, room: str, ttl_s: float, attributes: dict[str, str]) -> str:
    grants = VideoGrants(
        room=room, room_join=False, can_publish=False, can_subscribe=False, can_publish_data=False
    )
    return (
        AccessToken(signer.api_key, signer.secret)
        .with_identity(_identity(A_VISITOR))
        .with_grants(grants)
        .with_ttl(timedelta(seconds=ttl_s))
        .with_attributes(attributes)
        .to_jwt()
    )


def _identity(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(IDENTITY_BYTES)}"
