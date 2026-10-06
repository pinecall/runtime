"""The JWTs the gateway signs: a room token and its dispatch, a seat, a log token, a code token."""

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import jwt
from livekit.api import AccessToken, VideoGrants
from livekit.protocol.room import RoomConfiguration
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pinecall.domain.names import Env
from pinecall.domain.person import RoomScope
from pinecall.domain.scope import SCOPE_ATTRIBUTE, Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy.keys import (
    A_SEAT,
    A_SEAT_LASTS_S,
    A_VISITOR,
    AGENT_ATTRIBUTE,
    BOUND_TO_ONE_CALL,
    CODE_ATTRIBUTE,
    ENV_ATTRIBUTE,
    JWT_PARTS,
    MINTED,
    NAME_ATTRIBUTE,
    PROJECTION_ATTRIBUTE,
    SUBJECT_ATTRIBUTE,
    THE_MICROPHONE,
    Bearer,
)
from pinecall.wire.parts import Projection

# One statement on every open: the update is the spend, atomic (of two dispatches racing, one
# updates the row), and the join says what the ledger held before it. A token is spent only by the
# org, the world and the agent it was minted for; one minted before its world was kept (no env) is
# held to its org and agent.
SPEND = """
WITH spent AS (
    UPDATE tokens SET spent_at = now()
    WHERE call = %(call)s AND spent_at IS NULL AND org = %(org)s AND agent = %(agent)s
      AND (env IS NULL OR env = %(env)s)
    RETURNING call
)
SELECT (SELECT count(*) FROM spent) AS spent, tokens.call AS minted, tokens.spent_at
FROM (SELECT 1) AS probe LEFT JOIN tokens ON tokens.call = %(call)s
"""


IDENTITY_BYTES = 6


type Spending = Literal["spent", "already_spent", "never_minted", "minted_elsewhere"]


ONE_VISIT_TTL_S = 60


# The header of a token the gateway signs with its own key; a LiveKit token names no key id.
OUR_KEY_ID = "pinecall"


# Longer than any call, short enough that a copied link dies the same day.
READ_TTL_S = 4 * 60 * 60


@dataclass(frozen=True)
class Signer:
    """The LiveKit pair a room token is signed with, and the gateway's own key for every other."""

    api_key: str
    secret: str
    # A log token and a code token open no room, so LiveKit never reads them: they are signed with
    # this key, which no worker holds, and the pair every worker holds forges none.
    own: str


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


class _Answer(BaseModel):
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
    attributes: _Answer


@dataclass(frozen=True)
class SupervisorSeat:
    """A person's seat in a live call: the token and who it says they are."""

    call: str
    identity: str
    token: str
    subject: str | None
    name: str | None


@dataclass(frozen=True)
class MintedToken:
    """A room token as the ledger keeps it: one call, spent once."""

    call: str
    org: str
    env: Env
    agent: str
    scope: RoomScope
    expires_at: float


@dataclass(frozen=True, kw_only=True)
class Grant:
    """What a room token of one scope may do."""

    connects: bool
    audio: bool
    reads_log: bool
    own_call_only: bool
    ttl_s: int | None
    # `audio` publishes and subscribes; `hears` only subscribes; `hidden` is not seen in the room;
    # `writes` sends on the call's data channel, which the agent reads as the caller's words.
    hears: bool = False
    hidden: bool = False
    writes: bool = False


GRANTS: Mapping[RoomScope, Grant] = {
    # It reads its own call's log too, so a browser holds one token.
    "talk": Grant(
        connects=True,
        audio=True,
        writes=True,
        reads_log=True,
        own_call_only=True,
        ttl_s=ONE_VISIT_TTL_S,
    ),
    # It subscribes: livekit delivers its text streams to subscribers only.
    "chat": Grant(
        connects=True,
        audio=False,
        hears=True,
        writes=True,
        reads_log=True,
        own_call_only=True,
        ttl_s=ONE_VISIT_TTL_S,
    ),
    "observe": Grant(
        connects=False,
        audio=False,
        hears=True,
        hidden=True,
        reads_log=True,
        own_call_only=False,
        ttl_s=None,
    ),
    # Not hidden: livekit delivers no track of a hidden seat, so a takeover would be silent.
    "supervise": Grant(
        connects=False,
        audio=True,
        reads_log=True,
        own_call_only=False,
        ttl_s=None,
    ),
    "read": Grant(
        connects=False,
        audio=False,
        reads_log=True,
        own_call_only=True,
        ttl_s=READ_TTL_S,
    ),
    "participate": Grant(
        connects=False,
        audio=False,
        writes=True,
        reads_log=True,
        own_call_only=True,
        ttl_s=None,
    ),
}


# From the credential, never from a parameter: a scope that reads beyond its own call is the
# tenant's; an API key is the tenant.
PROJECTION_OF: Mapping[RoomScope, Projection] = {
    scope: "tenant" if grant.reads_log and not grant.own_call_only else "public"
    for scope, grant in GRANTS.items()
}


# What POST /v1/tokens may mint: the scopes that join a room.
MINTED_FOR_A_VISIT: frozenset[RoomScope] = frozenset(
    scope for scope, grant in GRANTS.items() if grant.connects
)


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
        # A widget writes to its call over the data channel; one that watches or listens does not.
        can_publish_data=grant.writes,
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


def seat(signer: Signer, call: str, scope: RoomScope, bearer: Bearer) -> SupervisorSeat:
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
    return SupervisorSeat(
        call=call, identity=identity, token=token, subject=bearer.key.subject, name=name
    )


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
        ours = jwt.get_unverified_header(token).get("kid") == OUR_KEY_ID
        decoded = jwt.decode(
            token,
            signer.own if ours else signer.secret,
            algorithms=["HS256"],
            options={"require": ["exp"]},
            leeway=0,
        )
        claims = _Claims.model_validate(decoded)
    except (jwt.PyJWTError, ValidationError):
        return None
    data = claims.attributes
    # A livekit token minted for another purpose with the same pair reads nothing here, and a
    # listener hears the room but reads no log. A reading token is the gateway's own key's alone:
    # one in its shape under the pair is a worker's forgery.
    if (
        not claims.video.room
        or data.scope not in BOUND_TO_ONE_CALL
        or ours != (data.scope == "read")
    ):
        return None
    return Visit(
        call="" if data.code is not None else claims.video.room,
        scope=data.scope,
        expires_at=claims.exp,
        identity=claims.sub,
        subject=data.subject,
        name=data.name,
        projection=data.projection,
        code=data.code,
        agent=data.agent,
        env=data.env,
    )


async def minted(pool: Pool, token: MintedToken) -> None:
    """Keep a room token the ledger will spend once, when its call opens."""
    values = {
        "call": token.call,
        "org": token.org,
        "env": token.env,
        "agent": token.agent,
        "scope": token.scope,
        "expires_at": token.expires_at,
    }
    async with pool.connection() as connection:
        await connection.execute(MINTED, values)


async def spend(pool: Pool, call: str, scope: Scope, agent: str) -> Spending:
    """Spend the call's token in the scope the worker names, once; otherwise say why not."""
    wanted = {"call": call, "org": scope.org, "env": scope.env, "agent": agent}
    async with pool.connection() as connection:
        row = await (await connection.execute(SPEND, wanted)).fetchone()
    if row is not None and row["spent"]:
        return "spent"
    if row is None or row["minted"] is None:
        return "never_minted"
    return "minted_elsewhere" if row["spent_at"] is None else "already_spent"


# The shape LiveKit gives a token (sub, exp, video, attributes), so one reader reads both; signed
# with the gateway's own key under its key id, and joining no room.
def _unjoinable(signer: Signer, room: str, ttl_s: float, attributes: dict[str, str]) -> str:
    now = int(time.time())
    claims = {
        "sub": _identity(A_VISITOR),
        "iat": now,
        "exp": now + int(ttl_s),
        "video": {"room": room, "roomJoin": False},
        "attributes": attributes,
    }
    return jwt.encode(claims, signer.own, algorithm="HS256", headers={"kid": OUR_KEY_ID})


def _identity(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(IDENTITY_BYTES)}"
