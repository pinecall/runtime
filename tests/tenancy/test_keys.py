"""Keys: kept as fingerprints, read with their person, in one world and scope; room tokens."""

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from livekit.api import TokenVerifier
from psycopg import AsyncConnection
from psycopg.abc import PQGen
from psycopg.rows import TupleRow

from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotFound, NotSignedIn
from pinecall.domain.org import Org
from pinecall.domain.person import SERVER_SCOPES, Key, Member
from pinecall.domain.scope import SCOPE_ATTRIBUTE, Scope
from pinecall.postgres.pool import Pool, connect, open_pool
from pinecall.tenancy.keys import (
    Bearer,
    Issued,
    check_agent,
    check_expiry,
    check_may_grant,
    check_opens,
    issue,
    listed,
    person_key,
    revoke,
    revoke_named,
    scope_of,
    server_scopes,
    verify,
    world_of,
)
from pinecall.tenancy.orgs import create
from pinecall.tenancy.people import Change, Invitee, fingerprint, invite, update
from pinecall.tenancy.tokens import (
    Signer,
    Visitor,
    room_token,
)
from tests.conftest import DSN, postgres

SIGNER = Signer("APIthisisatest", "a-livekit-secret-long-enough-for-hs256-signing")
ANA = Invitee(email="ana@clinica.test", name="Ana García", role="developer")
SERVER = Key("k_server", "org_1", env="production")
SANDBOX_SERVER = Key("k_ci", "org_1", env="sandbox")


def a_member(role: str = "developer", *, production: bool = False, org: str = "org_1") -> Member:
    return Member(
        id="m_ana",
        org=org,
        email="ana@clinica.test",
        name="Ana García",
        role="admin" if role == "admin" else "developer",
        status="active",
        production=production,
    )


def persons_of(member: Member) -> Bearer:
    key = Key("k_ana", member.org, scopes=member.scopes, subject=member.id, name=member.name)
    return Bearer(key=key, member=member)


async def org_with_keys(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


def in_a_minute() -> float:
    return time.time() + 60


LAST_STATEMENT = "select query from pg_stat_activity where pid = %s"


@postgres
async def test_the_row_holds_the_fingerprint_and_never_the_key(pool: Pool) -> None:
    org = await org_with_keys(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="production"))
    async with pool.connection() as connection:
        rows = await (await connection.execute("SELECT * FROM api_keys")).fetchall()
    assert [row["hash"] for row in rows] == [fingerprint(secret)]
    assert all(secret not in str(value) for value in rows[0].values())


@postgres
async def test_a_key_is_issued_into_one_world_with_the_scopes_and_the_person_asked_for(
    pool: Pool,
) -> None:
    org = await org_with_keys(pool)
    params = Issued(org=org.id, env="sandbox", scopes=frozenset({"app", "calls"}), name="ci")
    key, secret = await issue(pool, params)
    verified = await verify(pool, secret)
    assert verified is not None
    assert verified.key == key
    assert verified.member is None


# A statement alone on an autocommit connection: one execute, and no BEGIN or COMMIT around it.
# The backend's last statement says so from the server's side: a COMMIT would come after it.
@postgres
async def test_verifying_a_key_is_one_round_trip_and_no_transaction(
    schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = await open_pool(DSN, schema=schema, max_size=1)
    try:
        org = await org_with_keys(pool)
        issued = Issued(org=org.id, env="production", scopes=frozenset({"calls"}))
        _, secret = await issue(pool, issued)
        async with pool.connection() as connection:
            backend = connection.info.backend_pid
        executed: list[str] = []
        waited = AsyncConnection.wait

        async def counted[T](
            connection: AsyncConnection[TupleRow], gen: PQGen[T], interval: float = 0.1
        ) -> T:
            executed.append(getattr(gen, "__qualname__", ""))
            return await waited(connection, gen, interval)

        monkeypatch.setattr(AsyncConnection, "wait", counted)
        assert await verify(pool, secret) is not None
        monkeypatch.undo()
        async with await connect(DSN) as looking:
            last = await (await looking.execute(LAST_STATEMENT, (backend,))).fetchone()
    finally:
        await pool.close()
    assert [name for name in executed if name.endswith("_execute_gen")] == [
        "BaseCursor._execute_gen"
    ]
    assert last is not None
    assert str(last["query"]).lstrip().lower().startswith("with found as"), last["query"]


@postgres
async def test_a_key_no_row_answers_to_is_none_and_not_an_error(pool: Pool) -> None:
    assert await verify(pool, "nobody-minted-this") is None


@postgres
async def test_a_revoked_key_stops_verifying_and_its_row_stays_in_the_listing(pool: Pool) -> None:
    org = await org_with_keys(pool)
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
async def test_an_expired_key_is_refused_saying_so_and_one_with_time_left_still_opens(
    pool: Pool,
) -> None:
    org = await org_with_keys(pool)
    now = datetime.now(UTC)
    _, dead = await issue(pool, Issued(org=org.id, env="production", expires_at=now - timedelta(1)))
    _, alive = await issue(
        pool, Issued(org=org.id, env="production", expires_at=now + timedelta(1))
    )
    with pytest.raises(NotSignedIn, match="this key expired at 20"):
        await verify(pool, dead)
    assert await verify(pool, alive) is not None
    async with pool.connection() as connection:
        used = await (
            await connection.execute(
                "SELECT last_used_at FROM api_keys WHERE hash = %(hash)s",
                {"hash": fingerprint(dead)},
            )
        ).fetchone()
    assert used is not None
    assert used["last_used_at"] is None


def test_a_servers_token_opens_all_a_servers_scopes_or_some_of_them_and_nothing_else() -> None:
    assert server_scopes(None) == SERVER_SCOPES
    assert server_scopes(["knowledge"]) == frozenset({"knowledge"})
    for wanted in ([], ["knowledge", "team"], ["fleet"]):
        with pytest.raises(DeclarationRefused, match="a server's token opens some of"):
            server_scopes(wanted)


def test_an_expiry_is_a_moment_to_come() -> None:
    now = datetime.now(UTC)
    check_expiry(None, now)
    check_expiry(now + timedelta(days=30), now)
    with pytest.raises(DeclarationRefused, match="a moment to come"):
        check_expiry(now, now)


@postgres
async def test_a_verified_key_is_marked_used_at_most_once_a_minute(pool: Pool) -> None:
    org = await org_with_keys(pool)
    _, secret = await issue(pool, Issued(org=org.id, env="production"))
    assert (await listed(pool, org.id))[0].last_used_at is None
    await verify(pool, secret)
    first = (await listed(pool, org.id))[0].last_used_at
    await verify(pool, secret)
    assert first is not None
    assert (await listed(pool, org.id))[0].last_used_at == first


@postgres
async def test_a_key_and_its_person_are_read_in_one_statement(pool: Pool) -> None:
    org = await org_with_keys(pool)
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
    org = await org_with_keys(pool)
    other = await org_with_keys(pool, "northwind")
    await issue(pool, Issued(org=org.id, env="production", label="ours"))
    await issue(pool, Issued(org=other.id, env="production", label="theirs"))
    assert [row.key.label for row in await listed(pool, org.id)] == ["ours"]


@postgres
async def test_a_persons_one_key_carries_their_role_reads_them_back_and_never_expires(
    pool: Pool,
) -> None:
    org = await org_with_keys(pool)
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
async def test_a_disabled_persons_key_opens_nothing_even_before_it_is_revoked(pool: Pool) -> None:
    org = await org_with_keys(pool)
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


def test_a_servers_key_acts_in_its_own_world_and_refuses_the_other_by_name() -> None:
    assert world_of(Bearer(SERVER), None) == "production"
    assert world_of(Bearer(SANDBOX_SERVER), "sandbox") == "sandbox"
    with pytest.raises(NotAllowed, match="a production server's token"):
        world_of(Bearer(SERVER), "sandbox")


def test_a_persons_key_acts_in_the_sandbox_unless_it_asks_for_production() -> None:
    assert world_of(persons_of(a_member()), None) == "sandbox"
    with pytest.raises(NotAllowed, match="Ana García has no production access"):
        world_of(persons_of(a_member()), "production")
    assert world_of(persons_of(a_member(production=True)), "production") == "production"
    assert world_of(persons_of(a_member("admin")), "production") == "production"


def test_a_world_that_is_neither_is_refused() -> None:
    with pytest.raises(NotAllowed, match="not 'staging'"):
        world_of(persons_of(a_member()), "staging")


def test_production_is_the_orgs_corner_and_the_sandbox_is_each_persons() -> None:
    ana = persons_of(a_member())
    assert scope_of(ana, "production") == Scope("org_1", "production", "")
    assert scope_of(ana, "sandbox") == Scope("org_1", "sandbox", "m_ana")
    assert scope_of(Bearer(SANDBOX_SERVER), "sandbox") == Scope("org_1", "sandbox", "")


def test_only_a_key_that_sees_every_corner_opens_a_colleagues() -> None:
    colleague = Member(
        id="m_bo", org="org_1", email="bo@c.test", name="Bo", role="qa", status="active"
    )
    with pytest.raises(NotAllowed, match="only a key that sees every corner"):
        scope_of(persons_of(a_member()), "sandbox", looking_at=colleague)
    admin = persons_of(a_member("admin"))
    assert scope_of(admin, "sandbox", looking_at=colleague) == Scope("org_1", "sandbox", "m_bo")
    stranger = Member(id="m_x", org="org_2", email="x@c.test", name="X", role="qa", status="active")
    with pytest.raises(NotAllowed, match="no active member of this org"):
        scope_of(admin, "sandbox", looking_at=stranger)


def test_the_fleets_key_acts_in_the_corner_of_the_call_it_serves_in_its_own_world() -> None:
    fleet = Bearer(Key("k_fleet", "default", env="sandbox", scopes=frozenset({"fleet"})))
    call = Scope("org_1", "sandbox", "m_ana")
    assert scope_of(fleet, "sandbox", dispatched=call) == call
    with pytest.raises(DeclarationRefused, match="the fleet's key"):
        scope_of(fleet, "sandbox")
    with pytest.raises(NotAllowed, match="the sandbox fleet's key"):
        scope_of(fleet, "sandbox", dispatched=Scope("org_1", "production"))


def test_the_fleets_key_acts_in_the_scope_the_calls_head_keeps_whatever_the_dispatch_says() -> None:
    fleet = Bearer(Key("k_fleet", "default", env="sandbox", scopes=frozenset({"fleet"})))
    head = Scope("org_1", "sandbox", "m_ana")
    assert scope_of(fleet, "sandbox", called=head) == head
    assert scope_of(fleet, "sandbox", dispatched=head, called=head) == head
    with pytest.raises(NotFound, match="not in the scope the dispatch names"):
        scope_of(fleet, "sandbox", dispatched=Scope("org_2", "sandbox"), called=head)
    with pytest.raises(NotAllowed, match="the sandbox fleet's key"):
        scope_of(fleet, "sandbox", called=Scope("org_1", "production"))


def test_a_members_agents_bind_their_key_and_an_empty_list_is_every_agent() -> None:
    everyone = persons_of(a_member())
    check_agent(everyone, "recepcion")
    bound = persons_of(replace(a_member(), agents=frozenset({"recepcion"})))
    check_agent(bound, "recepcion")
    with pytest.raises(NotAllowed, match="Ana García works on recepcion, and agent ventas is not"):
        check_agent(bound, "ventas")
    check_agent(Bearer(Key("k_server", "org_1")), "ventas")


# A visit's member row is the visitor's own org's: its agents name none of the visited org's.
def test_a_members_agents_do_not_bind_a_visit_to_another_org() -> None:
    operator = replace(a_member(), agents=frozenset({"recepcion"}), operator=True)
    visiting = Bearer(Key("k_visit", "org_2", subject=operator.id), member=operator)
    check_agent(visiting, "ventas")


def test_a_door_refused_names_what_the_key_does_open() -> None:
    qa = Bearer(Key("k_qa", "org_1", scopes=frozenset({"calls", "evals"})))
    check_opens(qa, "calls")
    with pytest.raises(NotAllowed, match="does not open app: it opens calls · evals"):
        check_opens(qa, "app")


def test_a_role_is_granted_only_by_a_key_that_opens_everything_it_would() -> None:
    developer = persons_of(a_member())
    with pytest.raises(NotAllowed, match="cannot grant admin"):
        check_may_grant(developer, "admin", production=False)
    check_may_grant(developer, "qa", production=False)


def test_a_key_that_names_nobody_grants_what_it_is_asked_to() -> None:
    check_may_grant(Bearer(SERVER), "admin", production=True)


def test_production_is_given_only_by_somebody_who_acts_there() -> None:
    with pytest.raises(NotAllowed, match="cannot give it"):
        check_may_grant(persons_of(a_member()), None, production=True)
    check_may_grant(persons_of(a_member(production=True)), None, production=True)
    with pytest.raises(NotAllowed, match="cannot give it"):
        check_may_grant(Bearer(SANDBOX_SERVER), None, production=True)


def test_a_role_nobody_named_and_a_switch_left_off_are_not_asked_about() -> None:
    check_may_grant(Bearer(Key("k_nothing", "org_1", scopes=frozenset())), None, production=False)


def test_livekits_own_verifier_accepts_what_we_mint_and_reads_the_scope() -> None:
    token = room_token(SIGNER, "call_1", "supervise", Visitor(in_a_minute()))
    claims = TokenVerifier(SIGNER.api_key, SIGNER.secret).verify(token)
    assert claims.attributes is not None
    assert claims.attributes[SCOPE_ATTRIBUTE] == "supervise"


def test_talk_publishes_the_microphone_and_chat_publishes_none() -> None:
    verifier = TokenVerifier(SIGNER.api_key, SIGNER.secret)
    talk = verifier.verify(room_token(SIGNER, "c", "talk", Visitor(in_a_minute())))
    chat = verifier.verify(room_token(SIGNER, "c", "chat", Visitor(in_a_minute())))
    assert talk.video is not None
    assert chat.video is not None
    assert (talk.video.can_publish, talk.video.can_publish_sources) == (True, ["microphone"])
    assert (chat.video.can_publish, chat.video.can_subscribe) == (False, True)


def test_the_name_a_request_came_in_by_is_its_world_and_a_header_that_disagrees_is_refused() -> (
    None
):
    ana = persons_of(a_member(production=True))
    assert world_of(ana, None, at="production") == "production"
    assert world_of(ana, "production", at="production") == "production"
    with pytest.raises(NotAllowed, match="this name is the sandbox's: production answers"):
        world_of(ana, "production", at="sandbox")
    with pytest.raises(NotAllowed, match="a production server's token"):
        world_of(Bearer(SERVER), None, at="sandbox")


@postgres
async def test_a_machines_fleet_key_and_its_unspent_join_token_are_revoked_by_its_name(
    pool: Pool,
) -> None:
    org = await org_with_keys(pool)
    machine = "pinecall-worker-3"
    _, fleet_key = await issue(
        pool, Issued(org=org.id, env="sandbox", scopes=frozenset({"fleet"}), name=machine)
    )
    _, token = await issue(
        pool, Issued(org=org.id, env="sandbox", scopes=frozenset({"join"}), name=machine)
    )
    _, a_persons = await issue(pool, Issued(org=org.id, env="sandbox", name=machine))
    assert sorted(await revoke_named(pool, org.id, machine)) == sorted(
        [fingerprint(fleet_key), fingerprint(token)]
    )
    assert await revoke_named(pool, org.id, machine) == []
    assert (await verify(pool, fleet_key), await verify(pool, token)) == (None, None)
    assert await verify(pool, a_persons) is not None
