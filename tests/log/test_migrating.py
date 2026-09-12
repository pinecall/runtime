"""The guard around the numbered SQL: an edited migration, a missing one, and one lock at a time."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from pinecall.log.store.migrating import (
    MIGRATIONS_TABLE,
    POST_DEPLOY,
    RECORD_MIGRATION,
    SchemaRefused,
    a_hash,
    apply_migrations,
    every,
    ordered,
)
from pinecall.log.store.postgres import MIGRATIONS, connect
from tests.postgres import Dev

pytestmark = pytest.mark.postgres


async def a_schema(postgres: Dev) -> str:
    """An empty schema of this test's own, with the migrations table and nothing applied."""
    schema = f"pinecall_guard_{uuid4().hex[:12]}"
    connection = await connect(postgres.dsn)
    try:
        await connection.execute(f"create schema {schema}")
        await connection.execute(f"set search_path to {schema}")
        await connection.execute(MIGRATIONS_TABLE)
    finally:
        await connection.close()
    return schema


async def pretend_it_ran(postgres: Dev, schema: str, name: str, sha256: str | None) -> None:
    """A row in schema_migrations without the SQL: a database that says it ran something."""
    connection = await connect(postgres.dsn)
    try:
        await connection.execute(f"set search_path to {schema}")
        await connection.execute(RECORD_MIGRATION, name, sha256)
    finally:
        await connection.close()


async def test_a_migration_edited_after_it_ran_is_refused_by_name_and_by_both_hashes(
    postgres: Dev,
) -> None:
    """The one thing a name alone could never catch: two databases silently disagreeing."""
    schema = await a_schema(postgres)
    first = every()[0]
    await pretend_it_ran(postgres, schema, first.name, "a" * 64)

    with pytest.raises(SchemaRefused) as refused:
        await apply_migrations(postgres.dsn, schema=schema)

    said = str(refused.value)
    assert first.name in said
    assert "a" * 64 in said, "the hash this database ran"
    assert a_hash(first) in said, "and the one on disk now"
    assert "never edited" in said


async def test_a_row_with_no_hash_is_filled_in_rather_than_called_a_mismatch(
    postgres: Dev,
) -> None:
    """Everything applied before the column existed: nobody could have checked those."""
    schema = await a_schema(postgres)
    first = every()[0]
    await pretend_it_ran(postgres, schema, first.name, None)

    await apply_migrations(postgres.dsn, schema=schema)

    # And the next run checks it, which is the whole point of filling it in.
    connection = await connect(postgres.dsn)
    try:
        await connection.execute(f"set search_path to {schema}")
        kept = await connection.fetchval(
            "select sha256 from schema_migrations where name = $1", first.name
        )
    finally:
        await connection.close()
    assert kept == a_hash(first)


async def test_a_migration_the_database_ran_and_this_checkout_does_not_have_is_refused(
    postgres: Dev,
) -> None:
    """A checkout older than the database it is pointed at, said instead of migrated on top of."""
    schema = await a_schema(postgres)
    await pretend_it_ran(postgres, schema, "9999_from_the_future.sql", "b" * 64)

    with pytest.raises(SchemaRefused, match="older than the database"):
        await apply_migrations(postgres.dsn, schema=schema)


async def test_two_runs_at_once_do_not_both_migrate(postgres: Dev) -> None:
    """One box today; a hub and a worker starting together tomorrow. The lock is what holds."""
    schema = f"pinecall_race_{uuid4().hex[:12]}"

    both = await asyncio.gather(
        apply_migrations(postgres.dsn, schema=schema),
        apply_migrations(postgres.dsn, schema=schema),
    )

    ran = [name for one in both for name in one.applied]
    assert len(ran) == len(set(ran)), "no migration was applied twice"
    assert set(ran) == {path.name for path in ordered(post=False)}


async def test_a_run_says_which_database_it_talked_to_and_never_the_password(
    postgres: Dev,
) -> None:
    """The afternoon this exists to give back: two databases on one laptop, four apart."""
    schema = await a_schema(postgres)

    ran = await apply_migrations(postgres.dsn, schema=schema)

    assert ran.schema == schema
    assert ran.database and "@" not in ran.database
    assert "password" not in ran.database


def test_a_post_deployment_file_is_never_in_a_startup_run() -> None:
    """An index on a big table takes longer than the five seconds a startup migration is held to."""
    assert all(not path.name.endswith(POST_DEPLOY) for path in ordered(post=False))
    assert all(path.name.endswith(POST_DEPLOY) for path in ordered(post=True))
    assert set(ordered(post=False)) | set(ordered(post=True)) == set(every())


def test_the_lockfile_names_the_last_migration_there_is() -> None:
    """Adding one means bumping the lock, which is what makes two branches conflict IN GIT rather
    than merging cleanly and leaving one of them never to run. It is also the linter's baseline:
    what sits above this line has run nowhere and is gated; what is at or below it is history."""
    landed = [
        line.strip()
        for line in (MIGRATIONS / "migrations.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert landed == [every()[-1].name], (
        "migrations.lock is behind: bump it in the same commit as the migration"
    )


def test_no_two_migrations_share_a_number() -> None:
    """Two branches both adding `0022` merge cleanly and then one of them never runs."""
    numbers = [path.name.split("_")[0] for path in every()]
    assert len(numbers) == len(set(numbers)), f"a number is used twice: {sorted(numbers)}"


def test_every_migration_is_numbered_and_named() -> None:
    seen: list[Path] = every()
    assert seen, "the distribution ships migrations"
    for path in seen:
        number, _, rest = path.name.partition("_")
        assert number.isdigit() and len(number) == 4, path.name
        assert rest, f"{path.name} says a number and nothing about what it does"
    assert [path.name for path in seen] == sorted(path.name for path in MIGRATIONS.glob("*.sql"))


async def test_a_database_whose_table_predates_the_hashes_gets_the_column(postgres: Dev) -> None:
    """`create table if not exists` is a no-op on a database that already has one, so the column
    would never arrive and the first read of it would be an UndefinedColumnError mid-deploy."""
    schema = f"pinecall_old_table_{uuid4().hex[:12]}"
    connection = await connect(postgres.dsn)
    try:
        await connection.execute(f"create schema {schema}")
        await connection.execute(f"set search_path to {schema}")
        # The table exactly as it was before this commit.
        await connection.execute(
            "create table schema_migrations (name text primary key, applied_at timestamptz)"
        )
    finally:
        await connection.close()

    ran = await apply_migrations(postgres.dsn, schema=schema)

    assert ran.applied, "and every migration ran on top of it"
