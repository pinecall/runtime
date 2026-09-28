"""Tests for the JWTs the gateway signs: room, seat, log and code tokens."""

import time
from datetime import UTC, datetime, timedelta

from livekit.api import AccessToken, TokenVerifier

from pinecall.domain.person import Key
from pinecall.postgres.pool import Pool
from pinecall.tenancy.keys import Issued, issue, person_key, verify
from pinecall.tenancy.people import Change, invite, make_operator, update
from pinecall.tenancy.tokens import (
    GRANTS,
    MINTED_FOR_A_VISIT,
    PROJECTION_OF,
    MintedToken,
    Signer,
    Visitor,
    code_token,
    is_a_jwt,
    log_token,
    minted,
    read,
    room_token,
    seat,
    spend,
)
from tests.conftest import postgres
from tests.tenancy.test_keys import ANA, SIGNER, a_member, in_a_minute, org_with_keys, persons_of


@postgres
async def test_a_minted_key_is_new_every_time_says_its_world_and_whose_it_is(pool: Pool) -> None:
    org = await org_with_keys(pool)
    key, secret = await issue(pool, Issued(org=org.id, env="production", label="web"))
    _, again = await issue(pool, Issued(org=org.id, env="production", label="web"))
    _, sandboxed = await issue(pool, Issued(org=org.id, env="sandbox"))
    assert secret.startswith("pc_live_")
    assert sandboxed.startswith("pc_test_")
    assert secret != again
    assert (key.org, key.label, key.key_id[:2]) == (org.id, "web", "k_")


@postgres
async def test_a_key_minted_from_a_key_never_outlives_the_one_it_came_from(pool: Pool) -> None:
    org = await org_with_keys(pool)
    member = await update(
        pool,
        org.id,
        (await invite(pool, org.id, ANA, seats=None)).member.id,
        Change(status="active"),
    )
    parent = Key("k_parent", org.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
    key, _ = await person_key(pool, member, parent=parent)
    assert key.expires_at == parent.expires_at


@postgres
async def test_a_visit_to_another_org_opens_only_while_its_person_runs_the_box(pool: Pool) -> None:
    home = await org_with_keys(pool)
    visited = await org_with_keys(pool, "bidfire")
    member = await update(
        pool,
        home.id,
        (await invite(pool, home.id, ANA, seats=None)).member.id,
        Change(status="active"),
    )
    visit = Issued(org=visited.id, env="production", subject=member.id, name=member.name)
    _, secret = await issue(pool, visit)
    assert await verify(pool, secret) is None
    await make_operator(pool, home.id, member.id, on=True)
    verified = await verify(pool, secret)
    assert verified is not None
    assert verified.member is not None
    assert (verified.key.org, verified.member.operator) == (visited.id, True)
    await make_operator(pool, home.id, member.id, on=False)
    assert await verify(pool, secret) is None


def test_a_minted_token_reads_back_naming_the_call_as_its_room() -> None:
    token = room_token(SIGNER, "call_1", "talk", Visitor(expires_at=in_a_minute()))
    visit = read(SIGNER, token)
    assert visit is not None
    assert (visit.call, visit.scope) == ("call_1", "talk")


def test_a_token_carries_the_identity_it_was_minted_for_or_mints_a_visitor_one() -> None:
    named = read(SIGNER, room_token(SIGNER, "c", "talk", Visitor(in_a_minute(), identity="ana")))
    anonymous = read(SIGNER, room_token(SIGNER, "c", "talk", Visitor(in_a_minute())))
    assert named is not None
    assert anonymous is not None
    assert named.identity == "ana"
    assert anonymous.identity is not None
    assert anonymous.identity.startswith("web_")


def test_a_token_dies_on_time() -> None:
    assert read(SIGNER, room_token(SIGNER, "c", "talk", Visitor(time.time() - 1))) is None


def test_a_forged_or_edited_token_is_nothing() -> None:
    token = room_token(SIGNER, "c", "talk", Visitor(in_a_minute()))
    other = Signer(SIGNER.api_key, "another-secret-that-is-long-enough-to-sign")
    assert read(other, token) is None
    head, body, signature = token.split(".")
    assert read(SIGNER, f"{head}.{body}x.{signature}") is None


def test_a_token_without_our_scope_reads_nothing_here() -> None:
    token = room_token(SIGNER, "c", "talk", Visitor(in_a_minute()))
    stripped = AccessToken(SIGNER.api_key, SIGNER.secret).with_identity("someone").to_jwt()
    assert read(SIGNER, token) is not None
    assert read(SIGNER, stripped) is None


def test_an_observe_token_hears_a_room_and_opens_no_read_at_all() -> None:
    token = room_token(SIGNER, "c", "observe", Visitor(in_a_minute()))
    assert read(SIGNER, token) is None
    claims = TokenVerifier(SIGNER.api_key, SIGNER.secret).verify(token)
    assert claims.video is not None
    assert (claims.video.hidden, claims.video.can_subscribe, claims.video.can_publish) == (
        True,
        True,
        False,
    )


def test_every_scope_reads_through_exactly_one_projection_and_only_visits_are_minted() -> None:
    assert set(PROJECTION_OF) == set(GRANTS)
    assert PROJECTION_OF["supervise"] == "tenant"
    assert PROJECTION_OF["talk"] == "public"
    assert frozenset({"talk", "chat"}) == MINTED_FOR_A_VISIT


def test_a_token_is_told_from_a_key_by_its_shape_alone() -> None:
    assert is_a_jwt(room_token(SIGNER, "c", "talk", Visitor(in_a_minute())))
    assert not is_a_jwt("pc_live_abc")
    assert not is_a_jwt("a..b")


def test_a_log_token_reads_its_call_through_the_projection_it_was_minted_for() -> None:
    visit = read(SIGNER, log_token(SIGNER, "call_1", "tenant"))
    assert visit is not None
    assert (visit.call, visit.scope, visit.projection) == ("call_1", "read", "tenant")


def test_a_log_token_opens_no_room() -> None:
    claims = TokenVerifier(SIGNER.api_key, SIGNER.secret).verify(log_token(SIGNER, "c", "public"))
    assert claims.video is not None
    assert not claims.video.room_join


def test_a_code_token_names_its_code_and_no_call_and_dies_with_the_code() -> None:
    visit = read(SIGNER, code_token(SIGNER, "4821", "recepcion", "sandbox", in_a_minute()))
    assert visit is not None
    assert (visit.call, visit.code, visit.agent, visit.env) == ("", "4821", "recepcion", "sandbox")
    assert read(SIGNER, code_token(SIGNER, "4821", "recepcion", "sandbox", time.time() - 1)) is None


def test_a_seat_names_the_person_the_key_was_minted_for() -> None:
    taken = seat(SIGNER, "call_1", "supervise", persons_of(a_member()))
    visit = read(SIGNER, taken.token)
    assert visit is not None
    assert taken.identity.startswith("sup_")
    assert (visit.subject, visit.name, visit.identity) == ("m_ana", "Ana García", taken.identity)


@postgres
async def test_a_token_is_spent_once_and_the_ledger_remembers_it_was(pool: Pool) -> None:
    org = await org_with_keys(pool)
    await minted(pool, MintedToken("call_1", org.id, "recepcion", "talk", in_a_minute()))
    assert await spend(pool, "call_1") == "spent"
    assert await spend(pool, "call_1") == "already_spent"


@postgres
async def test_a_call_nobody_minted_a_token_for_is_told_apart_from_a_spent_one(
    pool: Pool,
) -> None:
    assert await spend(pool, "call_nobody") == "never_minted"
