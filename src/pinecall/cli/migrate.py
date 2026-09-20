"""`pinecall-runtime migrate`: the .sql files under pinecall/migrations, applied in order."""

import argparse
import asyncio

from pinecall._settings import load_settings
from pinecall.log.store.migrating import (
    POST_DEPLOY,
    Applied,
    SchemaRefused,
    apply_migrations,
    every,
    migrations_behind,
    ordered,
)
from pinecall.log.store.postgres import DEFAULT_SCHEMA, StoreUnreachable, create_pool

PURPOSE: str = "the database schema: up | status | plan"
VERBS: tuple[str, ...] = ("up", "status", "plan")

# The schema is applied here and nothing is minted here: `keys issue` is the one place a key
# exists in the clear, and a verb that a unit runs before every start must print no secret into
# a journal. 0006 seeds the `default` org; its first key is `pinecall-runtime keys issue`.
NO_KEY_YET = "org default has no key yet — `pinecall-runtime keys issue --org default` mints one"

# Said BEFORE anything is applied, and by `status` and `plan` too. A laptop with two databases on
# it and a `.env` naming one of them is how four migrations of difference become a 404 in a
# browser; a verb that says which database it is talking to is the whole of the fix.
AT = "at {database} · schema {schema}"

# Post-deployment files are named and never run at startup: an index on a big table takes longer
# than the five seconds a startup migration is held to. `migrate up --post` is a person's move.
WAITING = "{count} post-deployment migration(s) not run: `pinecall-runtime migrate up --post`"


def configure(parser: argparse.ArgumentParser) -> None:
    """One verb, and it is named. `migrate` alone used to mean `migrate up`: a person who typed it
    to see what it would do MIGRATED the database, which is the one default in this CLI that can
    change a box by being curious. It reads now, like every other group asked with no verb."""
    parser.add_argument("verb", nargs="?", default="status", choices=VERBS, help=" | ".join(VERBS))
    parser.add_argument(
        "--schema",
        default=DEFAULT_SCHEMA,
        help="apply into this schema instead of public (a test run owns its own copy)",
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help=f"the {POST_DEPLOY} files instead: what takes longer than a deploy may wait for",
    )
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Apply, or just say. Applying twice applies nothing: the record is the guard."""
    try:
        if arguments.verb == "plan":
            return _plan(arguments.post)
        if arguments.verb == "status":
            return asyncio.run(_status(arguments.schema))
        return asyncio.run(_migrate(arguments.schema, arguments.post))
    except (SchemaRefused, StoreUnreachable) as refused:
        # Not a traceback: this is a sentence a person acts on, and the exit code says it failed.
        # A database that does not answer is the commonest of the two, and it arrives here with
        # the password already out of the DSN (log/store/postgres.py, without_password).
        print(str(refused))
        return 1


async def _migrate(schema: str, post: bool) -> int:
    """The whole point of the verb: whatever this database has not run yet, in name order."""
    dsn = load_settings().database_url
    ran: Applied = await apply_migrations(dsn, schema=schema, post=post)
    print(AT.format(database=ran.database, schema=ran.schema))
    for name in ran.applied:
        print(f"applied {name}")
    if not ran.applied:
        print("nothing to apply — up to date")
    if ran.waiting:
        print(WAITING.format(count=len(ran.waiting)))
    # 0006 is the migration that seeds the org, so the run that applies it is the one run on
    # which the sentence is true.
    if any(name.endswith("_orgs.sql") for name in ran.applied):
        print(NO_KEY_YET)
    return 0


async def _status(schema: str) -> int:
    """What the DATABASE has, which is the question the verb's name asks — not what the disk has."""
    dsn = load_settings().database_url
    pool = await create_pool(dsn, schema=schema)
    try:
        behind = set(await migrations_behind(pool))
    finally:
        await pool.close()
    print(AT.format(database=dsn.split("@")[-1], schema=schema))
    for path in every():
        mark = "waiting" if path.name.endswith(POST_DEPLOY) else "applied"
        print(f"{'behind ' if path.name in behind else mark} {path.name}")
    return 0


def _plan(post: bool) -> int:
    """What a run of this kind WOULD apply, touching no database at all."""
    for path in ordered(post=post):
        print(path.name)
    return 0
