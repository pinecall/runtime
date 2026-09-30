"""`drift rebuild`: each sealed call's stages and verdicts counted into its day again."""

import argparse
import asyncio
import sys

from pinecall.cli._traceback import day_of
from pinecall.log import drift
from pinecall.log.drift import Rebuilt
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings

REBUILT = "{calls} sealed calls read, {folded} counted into their day's drift\n"


def drift_group(group: argparse.ArgumentParser) -> None:
    """`drift rebuild [--org <org id>] [--since YYYY-MM-DD]`."""
    verbs = group.add_subparsers(required=True)
    rebuilt = verbs.add_parser("rebuild", help="count the drift again from the log")
    rebuilt.add_argument("--org", default=None, help="an org's calls, by its id")
    rebuilt.add_argument("--since", default=None, help="the days since one, YYYY-MM-DD")
    rebuilt.set_defaults(run=rebuild)


def rebuild(settings: Settings, args: argparse.Namespace) -> int:
    """Forget the drift the flags name, every org's and day's when none, and count it again."""
    since = day_of(args.since)
    done = asyncio.run(_rebuilt(settings, args.org, since))
    sys.stdout.write(REBUILT.format(calls=done.calls, folded=done.folded))
    return 0


async def _rebuilt(settings: Settings, org: str | None, since: float | None) -> Rebuilt:
    pool = await open_pool(settings.database_url)
    try:
        return await drift.rebuild(pool, org=org, since=since)
    finally:
        await pool.close()
