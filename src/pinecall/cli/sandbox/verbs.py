"""`pinecall-runtime sandbox seed`: a new sandbox instance's rows, copied once from production."""

import argparse
import asyncio
import sys

from pinecall._settings import variable_of
from pinecall.cli.box.credentials import Decrypt, decrypt_with_systemd
from pinecall.cli.box.instance import INSTANCES, check_instance_name, credstore_of
from pinecall.cli.help import help_only
from pinecall.cli.sandbox.seed import seed
from pinecall.log.store import open_pool

PURPOSE: str = "a sandbox instance: seed it once from production"
VERBS: tuple[str, ...] = ("seed",)

DATABASE_URL = variable_of("database_url")


def configure(parser: argparse.ArgumentParser) -> None:
    """`sandbox seed --from-instance production --to-instance sandbox`, as root on the box."""
    verbs = parser.add_subparsers(dest="verb", metavar="verb")
    seeding = verbs.add_parser("seed", help="copy production's personas, sandbox knowledge, tuning")
    seeding.add_argument("--from-instance", default="production", help="default production")
    seeding.add_argument("--to-instance", default="sandbox", help="default sandbox")
    seeding.set_defaults(run=run_seed)
    parser.set_defaults(run=help_only(parser))


# By instance NAME, never by DSN: a DSN carries its password, and an argument is what `ps` shows
# every user of the box. Each is decrypted out of its own instance's store, which only root reads
# — and only root holds both, since the sandbox's role may connect to no other database.
def run_seed(arguments: argparse.Namespace) -> int:
    """Both databases out of their instances' stores, then the copy."""
    return asyncio.run(
        seed_sandbox(
            check_instance_name(arguments.from_instance),
            check_instance_name(arguments.to_instance),
            decrypt_with_systemd,
        )
    )


async def seed_sandbox(source: str, target: str, decrypt: Decrypt) -> int:
    """Open both, seed, close both."""
    production = await open_pool(
        decrypt(DATABASE_URL, credstore_of(source, INSTANCES) / DATABASE_URL)
    )
    try:
        sandbox = await open_pool(
            decrypt(DATABASE_URL, credstore_of(target, INSTANCES) / DATABASE_URL)
        )
        try:
            return await seed(production, sandbox, sys.stdout)
        finally:
            await sandbox.close()
    finally:
        await production.close()
