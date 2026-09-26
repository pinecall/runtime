"""The numbered SQL: running what a database has not run, and refusing what it cannot trust."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall.db.connecting import DEFAULT_SCHEMA, check_schema_name, connect, search_path_of
from pinecall.errors import PinecallError

# A migration that runs at STARTUP holds the gateway's door shut while it runs, so it is held to
# five seconds — Sentry's number, and for the same reason: a deploy must not be able to hang
# behind somebody's long query. Anything slower than this is not a startup migration; it is a
# `.post.sql`, which a person runs when they choose. And the lock timeout is the other half: a
# migration that cannot TAKE the lock in a second fails and is retried, rather than queueing
# behind a reader and blocking every writer that arrives after it.
# The .sql files, numbered, applied in name order, beside this runner. A migration is added, never
# edited. They are the RUNTIME's — the log's tables, auth's api_keys, memory's contact_memories and
# the knowledge base's tables sit in one schema, applied by one runner.
MIGRATIONS = Path(__file__).parent / "migrations"

STATEMENT_TIMEOUT_MS = 5_000
LOCK_TIMEOUT_MS = 1_000

# A post-deployment migration: an index on a big table, a backfill that walks rows. It is applied
# by `migrate up --post` and never by the unit that starts the gateway, so a deploy is never
# waiting on one. Named by the suffix so the file itself says which it is.
POST_DEPLOY = ".post.sql"

# A post-deployment file that opens with this line runs OUTSIDE a transaction, which is the only
# way `create index concurrently` runs at all: 0026 had to take the write lock instead. Such a
# file holds ONE statement — Postgres runs a multi-statement string as one implicit transaction,
# which is the very thing the marker opts out of — and is recorded once that statement is done.
NO_TRANSACTION = "-- pinecall:no-transaction"

# One number for the whole schema, so two processes starting at once do not both migrate. Any
# constant works as long as nothing else in this database picks the same one; it is this file's.
ADVISORY_LOCK = 0x9E3_C411

APPLIED_MIGRATIONS = "select name, sha256 from schema_migrations order by name"

RECORD_MIGRATION = "insert into schema_migrations (name, sha256) values ($1, $2)"

MIGRATIONS_TABLE = """
create table if not exists schema_migrations (
    name       text primary key,
    applied_at timestamptz not null default now(),
    sha256     text
)
"""

# The table above is `if not exists`, which on every database that already HAS one is a no-op —
# so the column would never arrive and the first read of it would be an UndefinedColumnError on
# a box mid-deploy. This is the one statement that cannot live in a numbered migration: the
# migration runner has to be able to read its own bookkeeping before it runs anything.
MIGRATIONS_TABLE_HAS_HASHES = "alter table schema_migrations add column if not exists sha256 text"

# 0021 and everything before it were recorded before the column existed. A row with no hash is not
# a mismatch — it is a row nobody could have checked — so it is filled in on the next run and
# checked from then on. What this must never do is silently accept a CHANGED file, which is why
# the fill is only ever for a NULL.
FILL_IN_A_HASH = "update schema_migrations set sha256 = $2 where name = $1 and sha256 is null"

# The one thing a name alone could never catch: a migration edited after it ran. Two databases
# then disagree about what their schema is and nothing anywhere says so. The rule against editing
# an applied migration is now a gate, and this is what it says when somebody trips it.
EDITED = (
    "migration {name} changed after it was applied: this database ran a different file.\n"
    "  applied  sha256 {was}\n"
    "  on disk  sha256 {now}\n"
    "An applied migration is never edited — every database that ran it has the OLD one. Put the\n"
    "change in a new migration, or restore the file."
)

# A file that is in the table and not on disk. Usually a checkout older than the database — a
# rollback, a branch — and the honest answer is to say so rather than to migrate on top of a
# schema this distribution does not describe.
MISSING = (
    "migration {name} is recorded as applied and is not in this distribution: "
    "this checkout is older than the database it is pointed at"
)


class MigrationsRefused(PinecallError):
    """This database and this distribution disagree about what has been applied."""


@dataclass(frozen=True)
class Applied:
    """What a run did: which database it did it to, and what it ran there."""

    database: str
    schema: str
    applied: tuple[str, ...]
    # Named but NOT run: the post-deployment files, when this was a startup run.
    waiting: tuple[str, ...]


async def apply_migrations(
    dsn: str, *, schema: str = DEFAULT_SCHEMA, post: bool = False
) -> Applied:
    """Run every .sql this database has not run, in name order, under one lock. What it ran."""
    name = check_schema_name(schema)
    connection: Any = await connect(dsn)
    try:
        # FIRST, before any DDL at all. The lock is session-level and belongs to no schema, so it
        # can be taken before the schema exists — and it has to be: two processes starting at the
        # same moment both ran `create schema if not exists` and one lost on the unique index over
        # pg_namespace, which is not a race `if not exists` protects you from. Released with the
        # connection, whatever happens.
        await connection.execute("select pg_advisory_lock($1)", ADVISORY_LOCK)
        if name != DEFAULT_SCHEMA:
            await connection.execute(f"create schema if not exists {name}")
        await connection.execute(f"set search_path to {search_path_of(name)}")
        await connection.execute(MIGRATIONS_TABLE)
        await connection.execute(MIGRATIONS_TABLE_HAS_HASHES)
        done = await _what_was_applied(connection)
        ran = [
            await _apply_one(connection, path)
            for path in migration_files(post=post)
            if path.name not in done
        ]
        return Applied(
            database=_the_database(dsn),
            schema=name,
            applied=tuple(ran),
            # What waits is what THIS DATABASE has not run, which the table above already
            # answered: naming every post file on disk sent a person to `migrate up --post` for
            # one they had applied by hand weeks ago (the box, 2026-09-20). `migrate status` was
            # told the same thing the same day, and they read one table between them now.
            waiting=tuple(path.name for path in migration_files(post=True) if path.name not in done)
            if not post
            else (),
        )
    finally:
        await connection.close()


def every() -> list[Path]:
    """Every migration this distribution ships, in the order they are applied."""
    return sorted(MIGRATIONS.glob("*.sql"))


def migration_files(*, post: bool) -> list[Path]:
    """What a run of this kind applies: the startup files, or the post-deployment ones."""
    return [path for path in every() if path.name.endswith(POST_DEPLOY) == post]


def file_hash(path: Path) -> str:
    """What was run, as a fingerprint of the bytes: the record a NAME could never be."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# The same question `apply_migrations` answers by doing it, asked without doing it: what a gateway
# checks at startup, because a process whose schema is behind does not fail at startup — it fails
# one door at a time, per request, as an UndefinedColumnError three frames deep. On a box nothing
# can be behind (the unit runs `migrate up` before every start); on a laptop nothing runs it for
# you, and this is the line that says so.
async def migrations_behind(pool: Any) -> tuple[str, ...]:
    """Every startup migration this database has not run. Empty is a schema that is level."""
    done = await migrations_applied(pool)
    return tuple(path.name for path in migration_files(post=False) if path.name not in done)


# What the TABLE says, post-deployment files included — `migrate status` marked every `.post.sql`
# "waiting" off the disk alone, so one a person had applied by hand still read as pending for ever
# (the box, 2026-09-20). One question, one answer, and the two readers above and below it agree.
async def migrations_applied(pool: Any) -> set[str]:
    """The name of every migration this database has run, whenever it ran it."""
    try:
        rows: Sequence[Any] = await pool.fetch(APPLIED_MIGRATIONS)
    except asyncpg.UndefinedTableError:
        # No table of its own yet: nothing has ever been applied. Any other refusal — a role
        # that may not read, a database that is not there — is said, never read as "behind".
        return set()
    return {str(row["name"]) for row in rows}


async def _what_was_applied(connection: Any) -> set[str]:
    """The names this database has run, refusing the two ways it can disagree with this disk."""
    on_disk = {path.name: path for path in every()}
    done: set[str] = set()
    for row in await connection.fetch(APPLIED_MIGRATIONS):
        name, was = str(row["name"]), row["sha256"]
        path = on_disk.get(name)
        if path is None:
            raise MigrationsRefused(MISSING.format(name=name))
        now = file_hash(path)
        if was is None:
            await connection.execute(FILL_IN_A_HASH, name, now)
        elif str(was) != now:
            raise MigrationsRefused(EDITED.format(name=name, was=str(was), now=now))
        done.add(name)
    return done


async def _apply_one(connection: Any, path: Path) -> str:
    """One migration and its record in one transaction: half a migration is never recorded."""
    sql = await asyncio.to_thread(path.read_text, encoding="utf-8")
    if not in_a_transaction(sql):
        await connection.execute(sql)
        await connection.execute(RECORD_MIGRATION, path.name, file_hash(path))
        return path.name
    async with connection.transaction():
        # Inside the transaction, so they are the migration's own and end with it. A post-deploy
        # file is the one kind that is allowed to take as long as it takes.
        if not path.name.endswith(POST_DEPLOY):
            await connection.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
        await connection.execute(f"set local lock_timeout = {LOCK_TIMEOUT_MS}")
        await connection.execute(sql)
        await connection.execute(RECORD_MIGRATION, path.name, file_hash(path))
    return path.name


def in_a_transaction(sql: str) -> bool:
    """Whether the runner wraps this file: every file, unless its first line opts out."""
    first = sql.lstrip().split("\n", 1)[0].strip()
    return first != NO_TRANSACTION


def _the_database(dsn: str) -> str:
    """Which database, for a person to read: host and name, and never the password in it.

    The afternoon this line exists to give back: a laptop with `pinecall` and `pinecall_dev` on
    it, a `.env` naming one and a hand naming the other, and four migrations of difference that
    showed up as a 404 in a browser.
    """
    without_secrets = dsn.split("@")[-1]
    return without_secrets or dsn
