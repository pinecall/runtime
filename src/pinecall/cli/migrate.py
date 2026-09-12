"""`pinecall-runtime migrate`: the .sql files under pinecall/migrations, applied in order."""

import argparse
import asyncio

from pinecall._settings import load_settings
from pinecall.log.store.migrating import apply_migrations
from pinecall.log.store.postgres import DEFAULT_SCHEMA, MIGRATIONS

PURPOSE: str = "the database schema: up | status"
VERBS: tuple[str, ...] = ("up", "status")

# The schema is applied here and nothing is minted here: `keys issue` is the one place a key
# exists in the clear, and a verb that a unit runs before every start must print no secret into
# a journal. 0006 seeds the `default` org; its first key is `pinecall-runtime keys issue`.
NO_KEY_YET = "org default has no key yet — `pinecall-runtime keys issue --org default` mints one"


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
    # 0006 is the migration that seeds the org, so the run that applies it is the one run on
    # which the sentence is true.
    if any(name.endswith("_orgs.sql") for name in applied):
        print(NO_KEY_YET)
    return 0


def _report_the_files() -> int:
    """What the distribution ships, without touching the database: the ordered list of files."""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        print(path.name)
    return 0
