"""`usage rebuild`: every org's usage totals folded again from the summaries in the log."""

import argparse
import asyncio
import sys

from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from pinecall.tenancy import usage

REBUILT = "{calls} summaries refolded into {rows} rows of usage_totals\n"


def usage_group(group: argparse.ArgumentParser) -> None:
    """`usage rebuild`."""
    verbs = group.add_subparsers(required=True)
    rebuilt = verbs.add_parser("rebuild", help="fold the usage totals again from the log")
    rebuilt.set_defaults(run=rebuild)


def rebuild(settings: Settings, _args: argparse.Namespace) -> int:
    """Refold every org's totals from the log and say how many summaries and rows."""
    refolded = asyncio.run(_rebuilt(settings))
    calls = sum(used.calls for used in refolded.values())
    sys.stdout.write(REBUILT.format(calls=calls, rows=len(refolded)))
    return 0


async def _rebuilt(settings: Settings) -> dict[usage.Month, usage.Usage]:
    pool = await open_pool(settings.database_url)
    try:
        return await usage.rebuild(pool)
    finally:
        await pool.close()
