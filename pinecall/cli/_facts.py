"""`facts rebuild`: each call's facts folded again from its log; doctor's question of the facts."""

import argparse
import asyncio
import sys

from pinecall.cli._traceback import day_of
from pinecall.domain.errors import PinecallError
from pinecall.log import refold
from pinecall.log.refold import Rebuilt, Refolding
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings

REBUILT = "{calls} calls refolded, {rewritten} facts rows rewritten\n"

BEHIND = "{logs}: rows past their head's seq"

APART = "facts not what their log folds to, of {sample} sampled: {calls}"


def facts_group(group: argparse.ArgumentParser) -> None:
    """`facts rebuild [--call <call>] [--org <org id>] [--since YYYY-MM-DD]`."""
    verbs = group.add_subparsers(required=True)
    rebuilt = verbs.add_parser("rebuild", help="fold the facts again from the log")
    rebuilt.add_argument("--call", default=None, help="one call")
    rebuilt.add_argument("--org", default=None, help="an org's calls, by its id")
    rebuilt.add_argument("--since", default=None, help="the calls started since a day, YYYY-MM-DD")
    rebuilt.set_defaults(run=rebuild)


def rebuild(settings: Settings, args: argparse.Namespace) -> int:
    """Refold the facts of the calls the flags name, every call's when none; say what changed."""
    refolding = Refolding(call=args.call, org=args.org, since=day_of(args.since))
    done = asyncio.run(_rebuilt(settings, refolding))
    sys.stdout.write(REBUILT.format(calls=done.calls, rewritten=done.rewritten))
    return 0


async def examined(settings: Settings) -> str | None:
    """What is wrong between the log and its facts: heads behind their rows, a sample refolded."""
    try:
        pool = await open_pool(settings.database_url)
    except PinecallError as refused:
        return str(refused)
    try:
        behind = await refold.heads_behind(pool)
        apart = await refold.differing(pool, sample=refold.A_SAMPLE)
    finally:
        await pool.close()
    troubles: list[str] = []
    if behind:
        troubles.append(BEHIND.format(logs=", ".join(behind)))
    if apart:
        calls = ", ".join(f"{item.call} ({', '.join(item.columns)})" for item in apart)
        troubles.append(APART.format(sample=refold.A_SAMPLE, calls=calls))
    return "; ".join(troubles) or None


async def _rebuilt(settings: Settings, refolding: Refolding) -> Rebuilt:
    pool = await open_pool(settings.database_url)
    try:
        return await refold.rebuild(pool, refolding)
    finally:
        await pool.close()
