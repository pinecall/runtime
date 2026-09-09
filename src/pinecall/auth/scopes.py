"""Which projection a caller reads through, and the room token that carries one call, one scope."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from livekit.api import AccessToken, TokenVerifier, VideoGrants
from livekit.protocol.room import RoomConfiguration

from pinecall._settings import Settings
from pinecall.auth.keys import KeyRecord, Keys
from pinecall.types.token import BOUND_TO_ONE_CALL, GRANTS, SCOPE_ATTRIBUTE, grant_for
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

# A call token IS a LiveKit room token whose room is the call. One format for text and for voice:
# the string that joins the room is the string that reads the call's log over SSE, so there is one
# minter, one verifier, and nothing for the two halves to disagree about. Which scope minted it
# rides a LiveKit attribute — SCOPE_ATTRIBUTE, in types/token.py — rather than a claim of our own:
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


def is_a_jwt(bearer: str) -> bool:
    """Whether this bearer is a JWT rather than an API key, by shape alone. Cheap, not a verdict."""
    return len(bearer.split(".")) == JWT_PARTS and all(bearer.split("."))


def verify_call_token(token: str, call: str, secret: LivekitKeys) -> CallToken | None:
    """The token, if it is real, unexpired and bound to THIS call. Anything else is None."""
    granted = a_call_token(token, secret)
    # The comparison is last on purpose: a token for another call is a real token, and answering
    # "not yours" before checking the signature would say so to somebody holding a forgery.
    return granted if granted is not None and granted.call == call else None


def a_call_token(token: str, secret: LivekitKeys) -> CallToken | None:
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
    scope = (claims.attributes or {}).get(SCOPE_ATTRIBUTE, "")
    # The scope is checked, not assumed: a perfectly valid LiveKit token minted for some other
    # purpose against the same pair opens a room; it does not open this tenant's log. An `observe`
    # token is not on that list either: a listener hears the room and reads nothing.
    if not room or scope not in BOUND_TO_ONE_CALL:
        return None
    return CallToken(
        call=room, scope=scope, expires_at=expires_at, identity=claims.identity or None
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


async def a_reader(bearer: str, keys: Keys, secret: LivekitKeys | None) -> Reader | None:
    """The bearer as who it is. None means nobody we know, and it learns nothing about why."""
    if is_a_jwt(bearer):
        granted = None if secret is None else a_call_token(bearer, secret)
        if granted is None:
            return None
        return Reader(
            projection=PROJECTION_OF[granted.scope], call=granted.call, viewer=granted.identity
        )
    record = await keys.verify(bearer)
    return None if record is None else Reader(projection=KEY_PROJECTION, key=record)


# The one minter. The grants are the scope's own row in types/token.py and nothing else: a talk
# token publishes its microphone and hears the agent, a chat token does neither, and every one of
# them may send data — the DataChannel is how a widget speaks to the call.
def a_room_token(
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
        .with_identity(identity or a_visitor())
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


def a_visitor() -> str:
    """An identity for a caller that arrived with none: the shape the chat door already mints."""
    return f"{A_VISITOR}{secrets.token_hex(VISITOR_BYTES)}"


# In production the pair is LiveKit's own, because the token IS a LiveKit token: the same string
# opens the room. In development a clone runs with a PINECALL_DEV_KEY and nothing else, so the pair
# is derived from it — enough to sign and verify our own reads, and a room it cannot open until
# LIVEKIT_API_KEY and LIVEKIT_API_SECRET are set, which is exactly what dev means.
def secret_for(settings: Settings) -> LivekitKeys:
    """The LiveKit pair call tokens are signed with, or a refusal when there is none."""
    if settings.livekit_api_key and settings.livekit_api_secret:
        return LivekitKeys(settings.livekit_api_key, settings.livekit_api_secret)
    if settings.dev_key:
        return LivekitKeys(
            api_key="devkey",
            api_secret=hashlib.sha256(f"participate:{settings.dev_key}".encode()).hexdigest(),
        )
    raise RuntimeError(
        "no LIVEKIT_API_KEY/LIVEKIT_API_SECRET and no PINECALL_DEV_KEY: "
        "nothing can verify a call token"
    )
