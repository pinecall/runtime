"""`pinecall-runtime migrate`: the .sql files under pinecall/migrations, applied in order."""

import argparse
import asyncio
import sys
from typing import TextIO

from pinecall._settings import load_settings
from pinecall.auth.keys import PostgresKeys
from pinecall.cli.keys.verbs import print_the_key
from pinecall.log.store import open_pool
from pinecall.log.store.postgres import DEFAULT_SCHEMA, MIGRATIONS, apply_migrations
from pinecall.types import DEFAULT_ORG

PURPOSE: str = "the database schema: up | status"
VERBS: tuple[str, ...] = ("up", "status")

# What the first key is for, so a `keys list` a year from now says where it came from.
THE_FIRST_KEY = "issued by migrate up"

# A box that already has one is a box that has been up before. Saying so is the whole of the
# idempotence: the key was printed once, on the run that issued it, and there is no second time.
ALREADY_ISSUED = "org {org} already has a key — `pinecall-runtime keys list` names its hash"


def configure(parser: argparse.ArgumentParser) -> None:
    """One verb, `up` by default: running migrations is what a person types `migrate` to do."""
    parser.add_argument("verb", nargs="?", default="up", choices=VERBS, help=" | ".join(VERBS))
    parser.add_argument(
        "--schema",
        default=DEFAULT_SCHEMA,
        help="apply into this schema instead of public (a test run owns its own copy)",
    )
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Apply, or just say what there is. Applying twice applies nothing: the record is the guard."""
    if arguments.verb == "status":
        return _report_the_files()
    return asyncio.run(_migrate(arguments.schema))


async def _migrate(schema: str) -> int:
    """The whole point of the verb: whatever this database has not run yet, in name order."""
    dsn = load_settings().database_url
    applied = await apply_migrations(dsn, schema=schema)
    if not applied:
        print(f"nothing to apply — {schema} is up to date")
    for name in applied:
        print(f"applied {name}")
    return await issue_the_default_orgs_key(dsn, schema)


# The promise CLAUDE.md and docs/decisions/routes.md both make: a fresh database comes out of
# `migrate up` with a `default` org whose key is on the operator's screen. Without it nothing can
# open /v1/routes and the box admits no worker and no app. The org itself is 0006's first row.
async def issue_the_default_orgs_key(dsn: str, schema: str, out: TextIO | None = None) -> int:
    """One key for the default org, on the run that finds none. Never a second one."""
    terminal = out or sys.stdout
    pool = await open_pool(dsn, schema=schema)
    try:
        keys = PostgresKeys(pool)
        if await keys.listed(DEFAULT_ORG):
            print(ALREADY_ISSUED.format(org=DEFAULT_ORG), file=terminal)
            return 0
        issued = await keys.issue(org=DEFAULT_ORG, label=THE_FIRST_KEY)
        print_the_key(issued, terminal)
        return 0
    finally:
        await pool.close()


def _report_the_files() -> int:
    """What the distribution ships, without touching the database: the ordered list of files."""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        print(path.name)
    return 0
