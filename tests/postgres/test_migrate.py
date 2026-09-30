"""Tests for the migration runner, on a real Postgres."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from pinecall.domain.errors import MigrationsRefused
from pinecall.postgres.migrate import (
    ADVISORY_LOCK,
    FIRST,
    MIGRATIONS,
    MIGRATIONS_TABLE,
    NO_TRANSACTION,
    RECORD,
    apply_migrations,
    file_hash,
    in_a_transaction,
    migration_files,
    migrations_behind,
)
from pinecall.postgres.pool import connect, database_named, open_pool

DSN = os.environ.get("DATABASE_URL", "")

postgres = pytest.mark.skipif(not DSN, reason="DATABASE_URL: a Postgres, `make test`")


# ── no database needed ──


def test_a_file_opting_out_of_the_transaction_says_so_on_its_first_line() -> None:
    assert not in_a_transaction(f"{NO_TRANSACTION}\ncreate index concurrently ...;\n".encode())
    assert not in_a_transaction(f"\n  {NO_TRANSACTION}  \ncreate index ...;\n".encode())
    assert in_a_transaction(b"-- a comment\ncreate index ...;\n")
    assert in_a_transaction(f"create index ...;\n{NO_TRANSACTION}\n".encode())


def test_every_migration_is_numbered_and_named_and_no_two_share_a_number() -> None:
    names = [path.name for path in migration_files()]
    assert names
    assert names[0] == FIRST
    for name in names:
        number, _, rest = name.partition("_")
        assert number.isdigit()
        assert len(number) == 4, name
        assert rest.removesuffix(".sql"), f"{name} says a number and nothing about what it does"
    numbers = [name[:4] for name in names]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers)), f"a number is used twice: {numbers}"


def test_the_first_migration_holds_no_extension_and_no_bookkeeping_table() -> None:
    text = migration_files()[0].read_text(encoding="utf-8").lower()
    assert "create extension" not in text
    assert "create table schema_migrations" not in text
    assert "public." not in text


def test_the_migrations_live_beside_the_runner_so_the_wheel_carries_them() -> None:
    package = Path(__file__).resolve().parents[2] / "pinecall" / "postgres"
    assert migration_files()[0].parent == package / "migrations"


# ── a real Postgres ──


@pytest.fixture
async def schema() -> AsyncIterator[str]:
    """A throwaway schema of this run, dropped whole at the end."""
    name = f"pinecall_test_{uuid4().hex[:12]}"
    yield name
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name))
        )


async def pretend_it_ran(schema: str, *recorded: tuple[str, str]) -> None:
    """Make the schema and record migrations as applied without running them."""
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("create schema if not exists {}").format(sql.Identifier(schema))
        )
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await connection.execute(MIGRATIONS_TABLE)
        for name, sha256 in recorded:
            await connection.execute(RECORD, (name, sha256))


async def column_of(schema: str, table: str, column: str) -> list[str]:
    async with await connect(DSN) as connection:
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        query = sql.SQL("select {} as it from {} order by 1").format(
            sql.Identifier(column), sql.Identifier(table)
        )
        rows = await (await connection.execute(query)).fetchall()
    return [str(row["it"]) for row in rows]


@postgres
async def test_the_schema_applies_on_an_empty_schema_and_a_second_run_applies_nothing(
    schema: str,
) -> None:
    first = await apply_migrations(DSN, schema=schema)
    again = await apply_migrations(DSN, schema=schema)

    assert first.applied == tuple(path.name for path in migration_files())
    assert again.applied == ()
    assert await column_of(schema, "schema_migrations", "name") == [
        path.name for path in migration_files()
    ]
    assert await column_of(schema, "orgs", "id") == ["default"], "the schema seeds the default org"


@postgres
async def test_a_migration_edited_after_it_ran_is_refused_by_name_and_by_both_hashes(
    schema: str,
) -> None:
    await pretend_it_ran(schema, (FIRST, "a" * 64))

    with pytest.raises(MigrationsRefused) as refused:
        await apply_migrations(DSN, schema=schema)

    data = str(refused.value)
    assert FIRST in data
    assert "a" * 64 in data, "the hash this database ran"
    assert file_hash(migration_files()[0]) in data, "and the one on disk now"
    assert "never edited" in data


@postgres
async def test_a_migration_the_database_ran_and_this_checkout_does_not_have_is_refused(
    schema: str,
) -> None:
    await pretend_it_ran(
        schema, (FIRST, file_hash(migration_files()[0])), ("9999_from_the_future.sql", "b" * 64)
    )

    with pytest.raises(MigrationsRefused, match="older than the database"):
        await apply_migrations(DSN, schema=schema)


# The previous runtime recorded 52 files; its schema is what 0001 holds.
@postgres
async def test_a_database_the_previous_runtime_migrated_is_taken_over_without_running_the_schema(
    schema: str,
) -> None:
    await pretend_it_ran(schema, ("0001_initial.sql", "c" * 64), ("0052_the_last.sql", "d" * 64))
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        await connection.execute((MIGRATIONS / FIRST).read_bytes())

    ran = await apply_migrations(DSN, schema=schema)

    later = [path.name for path in migration_files() if path.name != FIRST]
    assert ran.applied == tuple(later)
    assert await column_of(schema, "schema_migrations", "name") == [FIRST, *later]


# v1's own migration had added the dollar columns beside the euro ones before the takeover.
@postgres
async def test_a_v1_database_that_already_counts_in_dollars_keeps_its_dollars_and_loses_its_euros(
    schema: str,
) -> None:
    await pretend_it_ran(schema, ("0001_initial.sql", "c" * 64), ("0053_dollars.sql", "d" * 64))
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        await connection.execute((MIGRATIONS / FIRST).read_bytes())
        await connection.execute("alter table call_facts add column cost_usd double precision")
        await connection.execute("alter table quotas add column budget_usd integer")
        await connection.execute(
            "insert into call_facts (call, cost_eur, cost_usd) values ('CA_old', 2.0, null),"
            " ('CA_new', 9.0, 3.0)"
        )

    await apply_migrations(DSN, schema=schema)

    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        rows = await (
            await connection.execute("select call, cost_usd from call_facts order by call")
        ).fetchall()
        kept = await (
            await connection.execute(
                "select table_name, column_name from information_schema.columns"
                " where table_schema = %s and column_name in ('cost_eur', 'budget_eur')",
                (schema,),
            )
        ).fetchall()
    assert [(row["call"], row["cost_usd"]) for row in rows] == [("CA_new", 3.0), ("CA_old", 2.0)]
    assert kept == []


async def migrated_before(schema: str, number: str) -> None:
    """Run and record every migration numbered below this one, as a database that stood there."""
    before = [path for path in migration_files() if path.name[:4] < number]
    await pretend_it_ran(schema, *((path.name, file_hash(path)) for path in before))
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        for path in before:
            await connection.execute(path.read_bytes())


# The agents of an org: recepcion has settings in the sandbox, turnos a log the org claimed.
@postgres
async def test_an_orgs_lexicon_is_copied_to_each_of_its_agents_in_the_world_and_the_original_goes(
    schema: str,
) -> None:
    await migrated_before(schema, "0008")
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        await connection.execute(
            "insert into agent_config (org, env, holder, agent, version, config, author)"
            " values ('default', 'sandbox', '', 'recepcion', 1, '{}', 'm_ana')"
        )
        await connection.execute(
            "insert into call_log_head (log, agent, org) values ('@turnos', 'turnos', 'default')"
        )
        await connection.execute(
            "insert into lexicon (org, env, holder, version, heard, author) values"
            " ('default', 'sandbox', '', 3, '[\"Vidal\"]', 'm_ana'),"
            " ('default', 'production', '', 1, '[\"GSA\"]', 'm_ana')"
        )

    await apply_migrations(DSN, schema=schema)

    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        rows = await (
            await connection.execute(
                "select env, agent, version, heard from lexicon order by env, agent"
            )
        ).fetchall()
    assert [(row["env"], row["agent"], row["version"], row["heard"]) for row in rows] == [
        ("production", "turnos", 1, ["GSA"]),
        ("sandbox", "recepcion", 3, ["Vidal"]),
        ("sandbox", "turnos", 3, ["Vidal"]),
    ]


# An org's agents are the ones it tuned in either world and the ones whose own log it claimed.
@postgres
async def test_a_persona_becomes_one_copy_per_agent_it_named_or_per_agent_of_its_org(
    schema: str,
) -> None:
    await migrated_before(schema, "0009")
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        await connection.execute(
            "insert into orgs (id, slug, name) values ('acme', 'acme', 'Acme'),"
            " ('lonely', 'lonely', 'Lonely')"
        )
        await connection.execute(
            "insert into agent_config (org, env, holder, agent, version, config, author) values"
            " ('acme', 'sandbox', 'm_dev', 'desk', 1, '{}', 'a'),"
            " ('acme', 'production', '', 'sales', 1, '{}', 'a')"
        )
        await connection.execute(
            "insert into call_log_head (log, agent, call, org) values"
            " ('@billing', 'billing', null, 'acme'), ('CA_1', 'a-call', 'CA_1', 'acme')"
        )
        await connection.execute(
            "insert into agent_personas (org, name, goal, style, agents) values"
            " ('acme', 'anyone', 'g', 's', '{}'), ('acme', 'picky', 'g', 's', '{desk,elsewhere}'),"
            " ('lonely', 'nobody-to-call', 'g', 's', '{}')"
        )

    await apply_migrations(DSN, schema=schema)

    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("set search_path to {}, public").format(sql.Identifier(schema))
        )
        rows = await (
            await connection.execute(
                "select org, agent, name, goal from agent_personas order by name, agent"
            )
        ).fetchall()
        agents_column = await (
            await connection.execute(
                "select 1 from information_schema.columns where table_schema = %s"
                " and table_name = 'agent_personas' and column_name = 'agents'",
                (schema,),
            )
        ).fetchall()
    assert [(row["org"], row["agent"], row["name"]) for row in rows] == [
        ("acme", "billing", "anyone"),
        ("acme", "desk", "anyone"),
        ("acme", "sales", "anyone"),
        ("acme", "desk", "picky"),
        ("acme", "elsewhere", "picky"),
    ]
    assert {row["goal"] for row in rows} == {"g"}
    assert agents_column == []


@postgres
async def test_two_runs_at_once_do_not_both_migrate(schema: str) -> None:
    both = await asyncio.gather(
        apply_migrations(DSN, schema=schema), apply_migrations(DSN, schema=schema)
    )

    ran = [name for item in both for name in item.applied]
    assert len(ran) == len(set(ran)), "no migration was applied twice"
    assert set(ran) == {path.name for path in migration_files()}


# PGOPTIONS stands for a timeout the role or the database was given: libpq sends it on connect.
@postgres
async def test_a_run_waits_for_another_runner_whatever_timeout_the_session_was_given(
    schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked = asyncio.Event()

    async def another_runner() -> None:
        async with await connect(DSN) as connection:
            await connection.execute("select pg_advisory_lock(%s)", (ADVISORY_LOCK,))
            locked.set()
            await asyncio.sleep(0.4)
            await connection.execute("select pg_advisory_unlock(%s)", (ADVISORY_LOCK,))

    async def once_locked() -> tuple[str, ...]:
        await locked.wait()
        monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=100")
        return (await apply_migrations(DSN, schema=schema)).applied

    _, applied = await asyncio.gather(another_runner(), once_locked())
    assert applied == tuple(path.name for path in migration_files())


@postgres
async def test_a_run_says_which_database_it_talked_to_and_never_the_password(schema: str) -> None:
    ran = await apply_migrations(DSN, schema=schema)

    assert ran.schema == schema
    assert "@" not in ran.database
    assert ran.database == database_named(DSN)


@postgres
async def test_what_is_behind_is_every_file_before_the_first_run_and_nothing_after(
    schema: str,
) -> None:
    await pretend_it_ran(schema)
    pool = await open_pool(DSN, schema=schema)
    try:
        before = await migrations_behind(pool)
        await apply_migrations(DSN, schema=schema)
        after = await migrations_behind(pool)
    finally:
        await pool.close()

    assert before == tuple(path.name for path in migration_files())
    assert after == ()


@postgres
async def test_an_agents_judge_runs_on_every_call_or_simulations_of_an_org_that_exists(
    schema: str,
) -> None:
    await apply_migrations(DSN, schema=schema)
    insert = (
        "insert into agent_judges (org, agent, name, question, runs_on)"
        " values (%s, 'recepcion', 'greets', 'q', %s)"
    )
    async with await connect(DSN) as connection:
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await connection.execute(insert, ("default", "simulations"))
        with pytest.raises(psycopg.errors.CheckViolation):
            await connection.execute(insert, ("default", "sometimes"))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await connection.execute(insert, ("nobody", "every-call"))
    assert await column_of(schema, "agent_judges", "runs_on") == ["simulations"]


@postgres
async def test_a_release_is_numbered_from_one_of_an_app_hosted_in_a_world_that_exists(
    schema: str,
) -> None:
    await apply_migrations(DSN, schema=schema)
    app = (
        "insert into hosted_apps (org, env, name, key_fingerprint, sealed_key)"
        " values (%s, %s, 'support', 'f', 's')"
    )
    release = (
        "insert into hosted_releases (org, env, name, release, source, sha256, bytes)"
        " values ('default', 'production', %s, %s, 'x', 'h', 1)"
    )
    async with await connect(DSN) as connection:
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await connection.execute(app, ("default", "production"))
        with pytest.raises(psycopg.errors.CheckViolation):
            await connection.execute(app, ("default", "staging"))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await connection.execute(app, ("nobody", "sandbox"))
        await connection.execute(release, ("support", 1))
        with pytest.raises(psycopg.errors.CheckViolation):
            await connection.execute(release, ("support", 0))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await connection.execute(release, ("billing", 1))
    assert await column_of(schema, "hosted_releases", "release") == ["1"]
    assert await column_of(schema, "quotas", "hosted_apps") == []


@postgres
async def test_a_hosted_app_starts_with_nothing_live_and_nothing_failed(schema: str) -> None:
    await apply_migrations(DSN, schema=schema)
    app = (
        "insert into hosted_apps (org, env, name, key_fingerprint, sealed_key)"
        " values ('default', 'production', 'support', 'f', 's')"
    )
    async with await connect(DSN) as connection:
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await connection.execute(app)
    assert await column_of(schema, "hosted_apps", "live_release") == ["None"]
    assert await column_of(schema, "hosted_apps", "failed_host") == ["None"]
    assert await column_of(schema, "hosted_apps", "failed_why") == [""]


@postgres
async def test_the_time_an_app_served_is_a_count_per_day_of_an_org_that_exists(
    schema: str,
) -> None:
    await apply_migrations(DSN, schema=schema)
    insert = (
        "insert into hosted_usage (org, env, name, day, seconds)"
        " values (%s, 'production', 'support', '2026-09-30', %s)"
    )
    async with await connect(DSN) as connection:
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        await connection.execute(insert, ("default", 5.0))
        with pytest.raises(psycopg.errors.CheckViolation):
            await connection.execute(insert.replace("'support'", "'other'"), ("default", -1.0))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await connection.execute(insert, ("nobody", 1.0))
    assert await column_of(schema, "hosted_usage", "seconds") == ["5.0"]
