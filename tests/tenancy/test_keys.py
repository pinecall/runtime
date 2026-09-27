"""Keys: kept as fingerprints, read with their person, in one world and corner; room tokens."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from livekit.api import AccessToken, TokenVerifier
from psycopg import AsyncConnection

from pinecall.domain.errors import DeclarationRefused, NotAllowed
from pinecall.domain.types import SCOPE_ATTRIBUTE, Corner, Key, Member, Org
from pinecall.postgres.pool import Pool
from pinecall.tenancy.keys import (
    GRANTS,
    MINTED_FOR_A_VISIT,
    PROJECTION_OF,
    Bearer,
    Issued,
    Minted,
    Signer,
    Visitor,
    check_may_grant,
    check_opens,
    code_token,
    corner_of,
    is_a_jwt,
    issue,
    listed,
    log_token,
    minted,
    person_key,
    read,
    revoke,
    room_token,
    seat,
    spend,
    verify,
    world_of,
)
from pinecall.tenancy.orgs import create
from pinecall.tenancy.people import Change, Invitee, fingerprint, invite, make_operator, update
from tests.conftest import postgres

SIGNER = Signer("APIthisisatest", "a-livekit-secret-long-enough-for-hs256-signing")
ANA = Invitee(email="ana@clinica.test", name="Ana García", role="developer")
SERVER = Key("k_server", "org_1", env="production")
SANDBOX_SERVER = Key("k_ci", "org_1", env="sandbox")


def _member(role: str = "developer", *, production: bool = False, org: str = "org_1") -> Member:
    return Member(
        id="m_ana",
        org=org,
        email="ana@clinica.test",
        name="Ana García",
        role="admin" if role == "admin" else "developer",
        status="active",
        production=production,
    )


def _persons(member: Member) -> Bearer:
    key = Key("k_ana", member.org, scopes=member.scopes, subject=member.id, name=member.name)
    return Bearer(key=key, member=member)


async def _an_org(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


def _in_a_minute() -> float:
    return time.time() + 60


@postgres
async def test_a_minted_key_is_new_every_time_says_its_world_and_whose_it_is(pool: Pool) -> None:
    org = await _an_org(pool)
    key, secret = await issue(pool, Issued(org=org.id, env="production", label="web"))
    _, again = await issue(pool, Issued(org=org.id, env="production", label="web"))
    _, sandboxed = await issue(pool, Issued(org=org.id, env="sandbox"))
    assert secret.startswith("pc_live_")
    assert sandboxed.startswith("pc_test_")
    assert secret != again
    assert (key.org, key.label, key.key_id[:2]) == (org.id, "web", "k_")


@postgres
async def test_the_row_holds_the_fingerprint_and_never_the_key(pool: Pool) -> None:
    org = await _an_org(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="production"))
    async with pool.connection() as connection:
        rows = await (await connection.execute("SELECT * FROM api_keys")).fetchall()
    assert [row["hash"] for row in rows] == [fingerprint(secret)]
    assert all(secret not in str(value) for value in rows[0].values())


@postgres
async def test_a_key_is_issued_into_one_world_with_the_scopes_and_the_person_asked_for(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    asked = Issued(org=org.id, env="sandbox", scopes=frozenset({"app", "calls"}), name="ci")
    key, secret = await issue(pool, asked)
    verified = await verify(pool, secret)
    assert verified is not None
    assert verified.key == key
    assert verified.member is None


@postgres
async def test_a_key_no_row_answers_to_is_none_and_not_an_error(pool: Pool) -> None:
    assert await verify(pool, "nobody-minted-this") is None


@postgres
async def test_a_revoked_key_stops_verifying_and_its_row_stays_in_the_listing(pool: Pool) -> None:
    org = await _an_org(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="production"))
    assert await revoke(pool, fingerprint(secret))
    assert not await revoke(pool, fingerprint(secret))
    assert await verify(pool, secret) is None
    (row,) = await listed(pool, org.id)
    assert row.revoked_at is not None


@postgres
async def test_revoking_a_fingerprint_nobody_answers_to_is_false_and_not_an_error(
    pool: Pool,
) -> None:
    assert not await revoke(pool, fingerprint("nobody"))


@postgres
async def test_an_expired_key_is_nothing_and_one_with_time_left_still_opens(pool: Pool) -> None:
    org = await _an_org(pool)
    now = datetime.now(UTC)
    _, dead = await issue(pool, Issued(org=org.id, env="production", expires_at=now - timedelta(1)))
    _, alive = await issue(
        pool, Issued(org=org.id, env="production", expires_at=now + timedelta(1))
    )
    assert await verify(pool, dead) is None
    assert await verify(pool, alive) is not None


@postgres
async def test_a_verified_key_is_marked_used_at_most_once_a_minute(pool: Pool) -> None:
    org = await _an_org(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="production"))
    assert (await listed(pool, org.id))[0].last_used_at is None
    await verify(pool, secret)
    first = (await listed(pool, org.id))[0].last_used_at
    await verify(pool, secret)
    assert first is not None
    assert (await listed(pool, org.id))[0].last_used_at == first


@postgres
async def test_a_key_and_its_person_are_read_in_one_statement(pool: Pool) -> None:
    org = await _an_org(pool)
    member = await update(
        pool,
        org.id,
        (await invite(pool, org.id, ANA, seats=None)).member.id,
        Change(status="active"),
    )
    _, secret = await person_key(pool, member)
    real = AsyncConnection.execute
    with patch.object(AsyncConnection, "execute", autospec=True, side_effect=real) as spied:
        assert await verify(pool, secret) is not None
    assert spied.call_count == 1


@postgres
async def test_an_orgs_listing_holds_that_orgs_keys_and_nobody_elses(pool: Pool) -> None:
    org = await _an_org(pool)
    other = await _an_org(pool, "bidfire")
    await issue(pool, Issued(org=org.id, env="production", label="ours"))
    await issue(pool, Issued(org=other.id, env="production", label="theirs"))
    assert [row.key.label for row in await listed(pool, org.id)] == ["ours"]


@postgres
async def test_a_persons_one_key_carries_their_role_reads_them_back_and_never_expires(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    member = (await invite(pool, org.id, ANA, seats=None)).member
    member = await update(pool, org.id, member.id, Change(status="active"))
    key, secret = await person_key(pool, member)
    assert (key.env, key.scopes, key.subject, key.expires_at) == (
        "production",
        member.scopes,
        member.id,
        None,
    )
    verified = await verify(pool, secret)
    assert verified is not None
    assert verified.member == member


@postgres
async def test_a_key_minted_from_a_key_never_outlives_the_one_it_came_from(pool: Pool) -> None:
    org = await _an_org(pool)
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
async def test_a_disabled_persons_key_opens_nothing_even_before_it_is_revoked(pool: Pool) -> None:
    org = await _an_org(pool)
    member = await update(
        pool,
        org.id,
        (await invite(pool, org.id, ANA, seats=None)).member.id,
        Change(status="active"),
    )
    _, secret = await person_key(pool, member)
    async with pool.connection() as connection:
        await connection.execute("UPDATE members SET status = 'disabled'")
    assert await verify(pool, secret) is None


@postgres
async def test_a_visit_to_another_org_opens_only_while_its_person_runs_the_box(pool: Pool) -> None:
    home = await _an_org(pool)
    visited = await _an_org(pool, "bidfire")
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


def test_a_servers_key_acts_in_its_own_world_and_refuses_the_other_by_name() -> None:
    assert world_of(Bearer(SERVER), None) == "production"
    assert world_of(Bearer(SANDBOX_SERVER), "sandbox") == "sandbox"
    with pytest.raises(NotAllowed, match="a production server's token"):
        world_of(Bearer(SERVER), "sandbox")


def test_a_persons_key_acts_in_the_sandbox_unless_it_asks_for_production() -> None:
    assert world_of(_persons(_member()), None) == "sandbox"
    with pytest.raises(NotAllowed, match="Ana García has no production access"):
        world_of(_persons(_member()), "production")
    assert world_of(_persons(_member(production=True)), "production") == "production"
    assert world_of(_persons(_member("admin")), "production") == "production"


def test_a_world_that_is_neither_is_refused() -> None:
    with pytest.raises(NotAllowed, match="not 'staging'"):
        world_of(_persons(_member()), "staging")


def test_production_is_the_orgs_corner_and_the_sandbox_is_each_persons() -> None:
    ana = _persons(_member())
    assert corner_of(ana, "production") == Corner("org_1", "production", "")
    assert corner_of(ana, "sandbox") == Corner("org_1", "sandbox", "m_ana")
    assert corner_of(Bearer(SANDBOX_SERVER), "sandbox") == Corner("org_1", "sandbox", "")


def test_only_a_key_that_sees_every_corner_opens_a_colleagues() -> None:
    colleague = Member(
        id="m_bo", org="org_1", email="bo@c.test", name="Bo", role="qa", status="active"
    )
    with pytest.raises(NotAllowed, match="only a key that sees every corner"):
        corner_of(_persons(_member()), "sandbox", looking_at=colleague)
    admin = _persons(_member("admin"))
    assert corner_of(admin, "sandbox", looking_at=colleague) == Corner("org_1", "sandbox", "m_bo")
    stranger = Member(id="m_x", org="org_2", email="x@c.test", name="X", role="qa", status="active")
    with pytest.raises(NotAllowed, match="no active member of this org"):
        corner_of(admin, "sandbox", looking_at=stranger)


def test_the_fleets_key_acts_in_the_corner_of_the_call_it_serves_in_its_own_world() -> None:
    fleet = Bearer(Key("k_fleet", "default", env="sandbox", scopes=frozenset({"fleet"})))
    call = Corner("org_1", "sandbox", "m_ana")
    assert corner_of(fleet, "sandbox", dispatched=call) == call
    with pytest.raises(DeclarationRefused, match="the fleet's key"):
        corner_of(fleet, "sandbox")
    with pytest.raises(NotAllowed, match="the sandbox fleet's key"):
        corner_of(fleet, "sandbox", dispatched=Corner("org_1", "production"))


def test_a_door_refused_names_what_the_key_does_open() -> None:
    qa = Bearer(Key("k_qa", "org_1", scopes=frozenset({"calls", "evals"})))
    check_opens(qa, "calls")
    with pytest.raises(NotAllowed, match="does not open app: it opens calls · evals"):
        check_opens(qa, "app")


def test_a_role_is_granted_only_by_a_key_that_opens_everything_it_would() -> None:
    developer = _persons(_member())
    with pytest.raises(NotAllowed, match="cannot grant admin"):
        check_may_grant(developer, "admin", production=False)
    check_may_grant(developer, "qa", production=False)


def test_a_key_that_names_nobody_grants_what_it_is_asked_to() -> None:
    check_may_grant(Bearer(SERVER), "admin", production=True)


def test_production_is_given_only_by_somebody_who_acts_there() -> None:
    with pytest.raises(NotAllowed, match="cannot give it"):
        check_may_grant(_persons(_member()), None, production=True)
    check_may_grant(_persons(_member(production=True)), None, production=True)
    with pytest.raises(NotAllowed, match="cannot give it"):
        check_may_grant(Bearer(SANDBOX_SERVER), None, production=True)


def test_a_role_nobody_named_and_a_switch_left_off_are_not_asked_about() -> None:
    check_may_grant(Bearer(Key("k_nothing", "org_1", scopes=frozenset())), None, production=False)


def test_a_minted_token_reads_back_naming_the_call_as_its_room() -> None:
    token = room_token(SIGNER, "call_1", "talk", Visitor(expires_at=_in_a_minute()))
    visit = read(SIGNER, token)
    assert visit is not None
    assert (visit.call, visit.scope) == ("call_1", "talk")


def test_a_token_carries_the_identity_it_was_minted_for_or_mints_a_visitor_one() -> None:
    named = read(SIGNER, room_token(SIGNER, "c", "talk", Visitor(_in_a_minute(), identity="ana")))
    anonymous = read(SIGNER, room_token(SIGNER, "c", "talk", Visitor(_in_a_minute())))
    assert named is not None
    assert anonymous is not None
    assert named.identity == "ana"
    assert anonymous.identity is not None
    assert anonymous.identity.startswith("web_")


def test_a_token_dies_on_time() -> None:
    assert read(SIGNER, room_token(SIGNER, "c", "talk", Visitor(time.time() - 1))) is None


def test_a_forged_or_edited_token_is_nothing() -> None:
    token = room_token(SIGNER, "c", "talk", Visitor(_in_a_minute()))
    other = Signer(SIGNER.api_key, "another-secret-that-is-long-enough-to-sign")
    assert read(other, token) is None
    head, body, signature = token.split(".")
    assert read(SIGNER, f"{head}.{body}x.{signature}") is None


def test_a_token_without_our_scope_reads_nothing_here() -> None:
    token = room_token(SIGNER, "c", "talk", Visitor(_in_a_minute()))
    stripped = AccessToken(SIGNER.api_key, SIGNER.secret).with_identity("someone").to_jwt()
    assert read(SIGNER, token) is not None
    assert read(SIGNER, stripped) is None


def test_an_observe_token_hears_a_room_and_opens_no_read_at_all() -> None:
    token = room_token(SIGNER, "c", "observe", Visitor(_in_a_minute()))
    assert read(SIGNER, token) is None
    claims = TokenVerifier(SIGNER.api_key, SIGNER.secret).verify(token)
    assert claims.video is not None
    assert (claims.video.hidden, claims.video.can_subscribe, claims.video.can_publish) == (
        True,
        True,
        False,
    )


def test_livekits_own_verifier_accepts_what_we_mint_and_reads_the_scope() -> None:
    token = room_token(SIGNER, "call_1", "supervise", Visitor(_in_a_minute()))
    claims = TokenVerifier(SIGNER.api_key, SIGNER.secret).verify(token)
    assert claims.attributes is not None
    assert claims.attributes[SCOPE_ATTRIBUTE] == "supervise"


def test_talk_publishes_the_microphone_and_chat_publishes_none() -> None:
    verifier = TokenVerifier(SIGNER.api_key, SIGNER.secret)
    talk = verifier.verify(room_token(SIGNER, "c", "talk", Visitor(_in_a_minute())))
    chat = verifier.verify(room_token(SIGNER, "c", "chat", Visitor(_in_a_minute())))
    assert talk.video is not None
    assert chat.video is not None
    assert (talk.video.can_publish, talk.video.can_publish_sources) == (True, ["microphone"])
    assert (chat.video.can_publish, chat.video.can_subscribe) == (False, True)


def test_every_scope_reads_through_exactly_one_projection_and_only_visits_are_minted() -> None:
    assert set(PROJECTION_OF) == set(GRANTS)
    assert PROJECTION_OF["supervise"] == "tenant"
    assert PROJECTION_OF["talk"] == "public"
    assert frozenset({"talk", "chat"}) == MINTED_FOR_A_VISIT


def test_a_token_is_told_from_a_key_by_its_shape_alone() -> None:
    assert is_a_jwt(room_token(SIGNER, "c", "talk", Visitor(_in_a_minute())))
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
    visit = read(SIGNER, code_token(SIGNER, "4821", "recepcion", "sandbox", _in_a_minute()))
    assert visit is not None
    assert (visit.call, visit.code, visit.agent, visit.env) == ("", "4821", "recepcion", "sandbox")
    assert read(SIGNER, code_token(SIGNER, "4821", "recepcion", "sandbox", time.time() - 1)) is None


def test_a_seat_names_the_person_the_key_was_minted_for() -> None:
    taken = seat(SIGNER, "call_1", "supervise", _persons(_member()))
    visit = read(SIGNER, taken.token)
    assert visit is not None
    assert taken.identity.startswith("sup_")
    assert (visit.subject, visit.name, visit.identity) == ("m_ana", "Ana García", taken.identity)


@postgres
async def test_a_token_is_spent_once_and_the_ledger_remembers_it_was(pool: Pool) -> None:
    org = await _an_org(pool)
    await minted(pool, Minted("call_1", org.id, "recepcion", "talk", _in_a_minute()))
    assert await spend(pool, "call_1") == "spent"
    assert await spend(pool, "call_1") == "already_spent"


@postgres
async def test_a_call_nobody_minted_a_token_for_is_told_apart_from_a_spent_one(
    pool: Pool,
) -> None:
    assert await spend(pool, "call_nobody") == "never_minted"
