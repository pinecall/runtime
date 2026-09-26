"""Which projection a caller reads through, and the room token that carries one call, one scope."""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast, get_args

import jwt
from livekit.api import AccessToken, TokenVerifier, VideoGrants
from livekit.protocol.room import RoomConfiguration

from pinecall._settings import Settings
from pinecall.auth.keys import KeyRecord, Keys
from pinecall.types.key import ENVS, Env, parse_env
from pinecall.types.scopes import (
    AGENT_ATTRIBUTE,
    BOUND_TO_ONE_CALL,
    CODE_ATTRIBUTE,
    ENV_ATTRIBUTE,
    GRANTS,
    NAME_ATTRIBUTE,
    PROJECTION_ATTRIBUTE,
    SCOPE_ATTRIBUTE,
    SUBJECT_ATTRIBUTE,
    grant_for,
)
from pinecall_protocol.defs import Projection

# The only other place besides log/projection.py that spells the two projections: what a caller may
# see is decided HERE, from what it is, and never from a query parameter it sends. A client that
# could ask would be a client that could ask for more.
#
# And it is DERIVED, not a second table beside types/token.GRANTS: a scope that reads the log past
# its own call is reading the tenant's log; every other scope reads the public one. Two tables can
# drift apart on the day somebody adds a scope to one of them; a derivation cannot.
PROJECTION_OF: dict[str, Projection] = {
    scope: ("tenant" if grant.reads_log and not grant.own_call_only else "public")
    for scope, grant in GRANTS.items()
}

# An API key IS the tenant: observe, supervise and the dev key all read the tenant's own log.
KEY_PROJECTION: Projection = "tenant"

# The projections a token may name, as the wire spells them.
PROJECTIONS: tuple[str, ...] = get_args(Projection.__value__)

# A call token IS a LiveKit room token whose room is the call. One format for text and for voice:
# the string that joins the room is the string that reads the call's log over SSE, so there is one
# minter, one verifier, and nothing for the two halves to disagree about. Which scope minted it
# rides a LiveKit attribute — SCOPE_ATTRIBUTE, in types/scopes.py — rather than a claim of our own:
# attributes survive the round trip through TokenVerifier, and LiveKit publishes them to the room,
# so a participant's scope is readable on the media plane too without a second lookup.

# A JWT is three dot-separated parts; a Pinecall API key is 256 opaque bits with no dot in it. That
# is enough for the door to pick the verifier before it verifies either — the verify is the truth.
JWT_PARTS = 3

# A web caller minted no identity of its own, so the token gives it one. `web_` + 12 hex is what
# the chat door already mints for a visitor, and the log names the same shape as an event's author.
A_VISITOR = "web_"
VISITOR_BYTES = 6

# The one source a visitor may publish: its microphone. A camera or a screen has no place in a
# call, and livekit refuses at the media plane what the grant does not list.
THE_MICROPHONE = "microphone"

# No leeway. LiveKit allows a minute of clock skew because its tokens cross machines; this verifier
# runs in the same process that minted the token, so "expired" means expired.
NO_LEEWAY = timedelta(0)


@dataclass(frozen=True)
class LivekitKeys:
    """The LiveKit API key pair: what signs a call token, and what verifies one."""

    api_key: str
    api_secret: str


@dataclass(frozen=True)
class CallToken:
    """A verified call token: the one call it reads, which scope minted it, when it stops, who."""

    call: str
    scope: str
    expires_at: float
    identity: str | None = None
    # The person the seat was minted for, when a person's key minted it: the member's id, name.
    subject: str | None = None
    name: str | None = None
    # The projection a read token was minted for; None for every other scope, whose projection
    # is its grant's.
    projection: Projection | None = None
    # A code token names a code, an agent and a world instead of a call; `call` is "" for it.
    code: str | None = None
    agent: str | None = None
    env: Env | None = None


def is_a_jwt(bearer: str) -> bool:
    """Whether this bearer is a JWT rather than an API key, by shape alone. Cheap, not a verdict."""
    return len(bearer.split(".")) == JWT_PARTS and all(bearer.split("."))


def verify_call_token(token: str, call: str, secret: LivekitKeys) -> CallToken | None:
    """The token, if it is real, unexpired and bound to THIS call. Anything else is None."""
    granted = decode_call_token(token, secret)
    # The comparison is last on purpose: a token for another call is a real token, and answering
    # "not yours" before checking the signature would say so to somebody holding a forgery.
    return granted if granted is not None and granted.call == call else None


def decode_call_token(token: str, secret: LivekitKeys) -> CallToken | None:
    """The token, if it is real and unexpired, and the call it names. Which call is the caller's."""
    verifier = TokenVerifier(secret.api_key, secret.api_secret, leeway=NO_LEEWAY)
    try:
        claims = verifier.verify(token)
        # LiveKit's Claims drops `exp` on the way back, and the route above wants to know when the
        # token dies. The signature is already checked by the line above, so this reads a payload
        # nobody could have forged.
        # pyjwt ships no annotation for decode's key parameter, so strict mode cannot type the
        # call; the payload is a plain dict either way.
        payload: dict[str, Any] = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token, key="", algorithms=["HS256"], options={"verify_signature": False}
        )
        expires_at = float(payload["exp"])
    # Every way a token can be wrong — a bad signature, a missing exp, a wrong issuer, garbage that
    # is not a JWT at all — is the same answer at this door, and the caller learns nothing about
    # which. jwt raises its own family and the verifier raises ValueError; both mean no.
    except Exception:
        return None
    room = claims.video.room if claims.video is not None else ""
    attributes = claims.attributes or {}
    scope = attributes.get(SCOPE_ATTRIBUTE, "")
    # The scope is checked, not assumed: a perfectly valid LiveKit token minted for some other
    # purpose against the same pair opens a room; it does not open this tenant's log. An `observe`
    # token is not on that list either: a listener hears the room and reads nothing.
    if not room or scope not in BOUND_TO_ONE_CALL:
        return None
    projection = attributes.get(PROJECTION_ATTRIBUTE)
    if projection not in (None, *PROJECTIONS):
        return None
    # A code token's room is the code's own name, never a call: it reads no call at all.
    code = attributes.get(CODE_ATTRIBUTE) or None
    env = attributes.get(ENV_ATTRIBUTE) or None
    if env not in (None, *ENVS):
        return None
    return CallToken(
        call="" if code is not None else room,
        scope=scope,
        expires_at=expires_at,
        identity=claims.identity or None,
        subject=attributes.get(SUBJECT_ATTRIBUTE) or None,
        name=attributes.get(NAME_ATTRIBUTE) or None,
        projection=cast("Projection | None", projection),
        code=code,
        agent=attributes.get(AGENT_ATTRIBUTE) or None,
        env=None if env is None else parse_env(env),
    )


# One door for every log a reader can ask for: the SSE stream, the JSON page, the attach socket
# and the folded state. Two of them decided this separately once, and two rules about who may see
# what is one rule too many.
@dataclass(frozen=True)
class Reader:
    """Who is reading, and through which projection: an API key is a tenant, a token is a guest."""

    projection: Projection
    key: KeyRecord | None = None
    # A call token reads the one call it was minted for; the route compares and refuses.
    call: str | None = None
    # Whose own `event.received` entries reach them. A call token carries an identity, and it is
    # the same string the widget joins the LiveKit room under — so a guest sees the outside facts
    # it caused and nobody else's.
    viewer: str | None = None
    # Who this reader is when the token or the key was a person's: what a supervise verb is
    # written down as. None for an org's own key and for a visitor.
    subject: str | None = None
    name: str | None = None
    # The scope a token was minted with; None for a key.
    scope: str | None = None
    # A code token reads one code's standing and no call: `call` is "" and this names the code.
    code: str | None = None
    agent: str | None = None
    env: Env | None = None

    # A key steers when it opens the verbs (the door checks which), and a token only when its
    # scope's grant says so: a visitor's token and a page's read token read the call, never steer.
    @property
    def steers(self) -> bool:
        """Whether this reader may send the supervise verbs at all."""
        return self.key is not None or (
            self.scope is not None and grant_for(self.scope).sends_verbs
        )


async def reader_of_bearer(bearer: str, keys: Keys, secret: LivekitKeys | None) -> Reader | None:
    """The bearer as who it is. None means nobody we know, and it learns nothing about why."""
    if is_a_jwt(bearer):
        granted = None if secret is None else decode_call_token(bearer, secret)
        if granted is None:
            return None
        return Reader(
            projection=granted.projection or PROJECTION_OF[granted.scope],
            call=granted.call,
            viewer=granted.identity,
            subject=granted.subject,
            name=granted.name,
            scope=granted.scope,
            code=granted.code,
            agent=granted.agent,
            env=granted.env,
        )
    record = await keys.verify(bearer)
    if record is None:
        return None
    return Reader(projection=KEY_PROJECTION, key=record, subject=record.subject, name=record.name)


# The one minter. The grants are the scope's own row in types/scopes.py and nothing else: a talk
# token publishes its microphone and hears the agent, a chat token does neither, and every one of
# them may send data — the DataChannel is how a widget speaks to the call.
def mint_room_token(
    call: str,
    scope: str,
    expires_at: float,
    secret: LivekitKeys,
    identity: str | None = None,
    *,
    metadata: str = "",
    attributes: Mapping[str, str] | None = None,
    room_config: RoomConfiguration | None = None,
) -> str:
    """The signer `verify_call_token` checks against: one call, one scope, one visitor."""
    token = (
        AccessToken(secret.api_key, secret.api_secret)
        .with_identity(identity or new_visitor_identity())
        .with_grants(grants_of(scope, call))
        # A TTL, not an epoch: LiveKit stamps exp itself. A negative one is a token already
        # dead when it is minted, which is how a test asks for an expired one.
        .with_ttl(timedelta(seconds=expires_at - time.time()))
        .with_attributes({**(attributes or {}), SCOPE_ATTRIBUTE: scope})
    )
    if metadata:
        token = token.with_metadata(metadata)
    if room_config is not None:
        token = token.with_room_config(room_config)
    return token.to_jwt()


# The read token: one call's log and its recording, for as long as a page shows the call. Its grant
# names the room — the call — and does not let it join, so LiveKit refuses it at the media plane
# and every door of ours reads the call it is bound to exactly as it reads a room token's.
def mint_log_token(
    call: str, projection: Projection, secret: LivekitKeys, identity: str | None = None
) -> str:
    """A token that reads that call's log, through that projection, and opens nothing else."""
    return (
        AccessToken(secret.api_key, secret.api_secret)
        .with_identity(identity or new_visitor_identity())
        .with_grants(
            VideoGrants(
                room=call,
                room_join=False,
                can_publish=False,
                can_subscribe=False,
                can_publish_data=False,
            )
        )
        .with_ttl(timedelta(seconds=grant_for("read").ttl_s or 0))
        .with_attributes({SCOPE_ATTRIBUTE: "read", PROJECTION_ATTRIBUTE: projection})
        .to_jwt()
    )


# The code token: what a page asks "has my code been claimed yet?" with. It names the code and no
# call, opens no room, and dies with the code. Once a call claims the code, the standing door
# mints a real log token for that call, and this one has nothing left to say.
def mint_code_token(code: str, agent: str, env: Env, expires_at: float, secret: LivekitKeys) -> str:
    """A token that reads one code's standing, and nothing else, until the code expires."""
    return (
        AccessToken(secret.api_key, secret.api_secret)
        .with_identity(new_visitor_identity())
        .with_grants(
            VideoGrants(
                room=f"code:{code}",
                room_join=False,
                can_publish=False,
                can_subscribe=False,
                can_publish_data=False,
            )
        )
        .with_ttl(timedelta(seconds=expires_at - time.time()))
        .with_attributes(
            {
                SCOPE_ATTRIBUTE: "read",
                CODE_ATTRIBUTE: code,
                AGENT_ATTRIBUTE: agent,
                ENV_ATTRIBUTE: env,
            }
        )
        .to_jwt()
    )


def grants_of(scope: str, call: str) -> VideoGrants:
    """The grants a scope's row allows: join the one room, audio as the row says, data always."""
    grant = grant_for(scope)
    return VideoGrants(
        room=call,
        room_join=True,
        can_publish=grant.audio,
        can_subscribe=grant.audio or grant.hears,
        can_publish_data=True,
        can_publish_sources=[THE_MICROPHONE] if grant.audio else None,
        hidden=grant.hidden,
    )


def new_visitor_identity() -> str:
    """An identity for a caller that arrived with none: the shape the chat door already mints."""
    return f"{A_VISITOR}{secrets.token_hex(VISITOR_BYTES)}"


# The pair is LiveKit's own, because the token IS a LiveKit token: the same string opens the room.
# A second pair used to be derived from PINECALL_DEV_KEY, which signed tokens that opened no room —
# a gateway that looked like it worked and could not carry a call. The dev stack brings LiveKit up
# beside Postgres, so there is one pair and it is the real one.
NO_LIVEKIT_PAIR = "no LIVEKIT_API_KEY/LIVEKIT_API_SECRET: nothing can sign or verify a call token"


def secret_for(settings: Settings) -> LivekitKeys:
    """The LiveKit pair call tokens are signed with, or a refusal when there is none."""
    if settings.livekit_api_key and settings.livekit_api_secret:
        return LivekitKeys(settings.livekit_api_key, settings.livekit_api_secret)
    raise RuntimeError(NO_LIVEKIT_PAIR)
