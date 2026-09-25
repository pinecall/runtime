"""The call token: a LiveKit room token whose room is the call and whose scope is an attribute."""

import time

import pytest
from livekit.api import AccessToken, TokenVerifier, VideoGrants

from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.scopes import (
    KEY_PROJECTION,
    PROJECTION_OF,
    SCOPE_ATTRIBUTE,
    THE_MICROPHONE,
    LivekitKeys,
    a_call_token,
    a_code_token,
    a_log_token,
    a_reader,
    a_room_token,
    grants_of,
    is_a_jwt,
    verify_call_token,
)

pytestmark = pytest.mark.unit

THE_PAIR = LivekitKeys(api_key="APItesting", api_secret="a-secret-nobody-will-ever-deploy")
ANOTHER_PAIR = LivekitKeys(api_key="APItesting", api_secret="a-different-secret-entirely-and-long")
A_CALL = "CA_8f4a2c"
A_KEY = "pk_test_a_key_nobody_will_ever_deploy"
A_MINUTE = 60.0


def a_token(
    call: str = A_CALL,
    scope: str = "participate",
    lasting: float = A_MINUTE,
    identity: str | None = None,
) -> str:
    """One freshly minted call token, said once so every sentence below reads as one."""
    return a_room_token(call, scope, time.time() + lasting, THE_PAIR, identity)


def test_a_minted_token_verifies_and_names_the_call_as_its_room() -> None:
    granted = a_call_token(a_token(), THE_PAIR)
    assert granted is not None and granted.call == A_CALL
    assert granted.scope == "participate"
    assert granted.expires_at > time.time()


def test_a_token_carries_the_identity_it_was_minted_for_or_mints_a_visitor_one() -> None:
    """The identity is who the log calls this reader, and who the room calls them too."""
    named = a_call_token(a_token(identity="web_deadbeef1234"), THE_PAIR)
    assert named is not None and named.identity == "web_deadbeef1234"
    anonymous = a_call_token(a_token(), THE_PAIR)
    assert anonymous is not None and anonymous.identity is not None
    assert anonymous.identity.startswith("web_")


def test_a_token_dies_on_time() -> None:
    """A TTL in the past is a token dead the moment it is minted; no leeway forgives it."""
    assert a_call_token(a_token(lasting=-A_MINUTE), THE_PAIR) is None


def test_a_token_for_another_call_is_refused_by_verify_call_token() -> None:
    """It is a real token — the signature holds — and it still reads nothing but its own call."""
    token = a_token()
    assert verify_call_token(token, A_CALL, THE_PAIR) is not None
    assert verify_call_token(token, "CA_someone_else", THE_PAIR) is None


def test_a_forged_or_edited_token_is_nothing() -> None:
    token = a_token()
    assert a_call_token(token, ANOTHER_PAIR) is None
    header, payload, signature = token.split(".")
    assert a_call_token(f"{header}.{payload}x.{signature}", THE_PAIR) is None
    assert a_call_token("not-a-token-at-all", THE_PAIR) is None
    assert a_call_token("a.b.c", THE_PAIR) is None


def test_a_token_without_our_scope_reads_nothing_here() -> None:
    """A LiveKit token minted elsewhere opens a room; it does not open this tenant's log."""
    elsewhere = (
        AccessToken(THE_PAIR.api_key, THE_PAIR.api_secret)
        .with_identity("web_someone")
        .with_grants(VideoGrants(room=A_CALL, room_join=True))
        .to_jwt()
    )
    assert a_call_token(elsewhere, THE_PAIR) is None


def test_a_supervise_token_is_bound_to_the_one_call_it_was_minted_for() -> None:
    """The desk's token reads that call and sends its verbs; observe still takes the API key."""
    supervising = _a_room_token_for("supervise")
    granted = a_call_token(supervising, THE_PAIR)
    assert granted is not None
    assert (granted.call, granted.scope, granted.identity) == (A_CALL, "supervise", "ana")
    assert verify_call_token(supervising, "call_somebody_elses", THE_PAIR) is None


def test_an_observe_token_hears_a_room_and_opens_no_read_at_all() -> None:
    """A listener's token is a seat in the room, and POST /listen mints it with the API key."""
    assert a_call_token(_a_room_token_for("observe"), THE_PAIR) is None


def _a_room_token_for(scope: str) -> str:
    """One room token minted by hand under a scope, without going through a door."""
    return (
        AccessToken(THE_PAIR.api_key, THE_PAIR.api_secret)
        .with_identity("ana")
        .with_grants(VideoGrants(room=A_CALL, room_join=True))
        .with_attributes({SCOPE_ATTRIBUTE: scope})
        .to_jwt()
    )


def test_livekits_own_verifier_accepts_what_we_mint_and_reads_the_scope() -> None:
    """The same jwt, verified by the library, joins the room."""
    claims = TokenVerifier(THE_PAIR.api_key, THE_PAIR.api_secret).verify(a_token(scope="talk"))
    assert claims.video is not None and claims.video.room == A_CALL
    assert claims.video.room_join is True
    assert (claims.attributes or {})[SCOPE_ATTRIBUTE] == "talk"


def test_talk_publishes_the_microphone_and_chat_publishes_none() -> None:
    """The scope's own row: a chat token publishes nothing, subscribes, and sends data."""
    talk = grants_of("talk", A_CALL)
    assert (talk.room, talk.room_join, talk.can_publish, talk.can_subscribe) == (
        A_CALL,
        True,
        True,
        True,
    )
    assert talk.can_publish_sources == [THE_MICROPHONE]
    chat = grants_of("chat", A_CALL)
    assert (chat.can_publish, chat.can_subscribe, chat.can_publish_sources) == (False, True, None)
    assert talk.can_publish_data and chat.can_publish_data


def test_a_token_is_told_from_a_key_by_its_shape_alone() -> None:
    """The door picks the verifier before it verifies: a key never reaches the JWT machinery."""
    assert is_a_jwt(a_token())
    assert not is_a_jwt(A_KEY)
    assert not is_a_jwt("missing.parts")
    assert not is_a_jwt("empty..parts")


async def test_an_api_key_is_untouched_by_any_of_this() -> None:
    """A key is still a key: it reads the tenant's projection and names no call."""
    record = KeyRecord(key_id="k_1", org="clinica")
    reader = await a_reader(A_KEY, MemoryKeys({A_KEY: record}), THE_PAIR)
    assert reader is not None and reader.projection == KEY_PROJECTION
    assert reader.key == record and reader.call is None


@pytest.mark.parametrize("scope", ["participate", "talk", "chat"])
async def test_a_call_token_at_the_same_door_reads_its_own_call_as_a_guest(scope: str) -> None:
    """One door, two bearers: the token becomes the guest reader the sinks project through."""
    token = a_token(scope=scope, identity="web_abc123abc123")
    reader = await a_reader(token, MemoryKeys(), THE_PAIR)
    assert reader is not None and reader.projection == PROJECTION_OF[scope] == "public"
    assert reader.call == A_CALL and reader.viewer == "web_abc123abc123"


def test_a_log_token_reads_its_call_through_the_projection_it_was_minted_for() -> None:
    granted = a_call_token(a_log_token(A_CALL, "tenant", THE_PAIR), THE_PAIR)
    assert granted is not None and granted.call == A_CALL and granted.scope == "read"
    assert granted.projection == "tenant"
    assert granted.expires_at > time.time() + 3 * 60 * 60


def test_a_log_token_opens_no_room() -> None:
    claims = TokenVerifier(THE_PAIR.api_key, THE_PAIR.api_secret).verify(
        a_log_token(A_CALL, "public", THE_PAIR)
    )
    assert claims.video is not None and claims.video.room == A_CALL
    assert not claims.video.room_join and not claims.video.can_publish
    assert not claims.video.can_subscribe and not claims.video.can_publish_data


def test_a_code_token_names_its_code_and_no_call_and_dies_with_the_code() -> None:
    token = a_code_token("4821", "clinica-norte", "production", time.time() + A_MINUTE, THE_PAIR)
    granted = a_call_token(token, THE_PAIR)
    assert granted is not None and granted.call == "" and granted.scope == "read"
    assert (granted.code, granted.agent, granted.env) == ("4821", "clinica-norte", "production")
    assert granted.expires_at <= time.time() + A_MINUTE
    assert verify_call_token(token, "code:4821", THE_PAIR) is None
    dead = a_code_token("4821", "clinica-norte", "production", time.time() - 1, THE_PAIR)
    assert a_call_token(dead, THE_PAIR) is None


async def test_a_reader_from_a_log_token_reads_and_never_steers() -> None:
    reader = await a_reader(a_log_token(A_CALL, "tenant", THE_PAIR), MemoryKeys(), THE_PAIR)
    assert reader is not None and reader.call == A_CALL and reader.projection == "tenant"
    assert not reader.steers
    visitor = await a_reader(a_token(scope="talk"), MemoryKeys(), THE_PAIR)
    assert visitor is not None and not visitor.steers
    desk = await a_reader(a_token(scope="supervise"), MemoryKeys(), THE_PAIR)
    assert desk is not None and desk.steers
