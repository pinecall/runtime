"""`pinecall-runtime memory reembed`: facts another embedder wrote, written again by this one."""

from __future__ import annotations

import argparse
import asyncio

import httpx

from pinecall.db import DEFAULT_SCHEMA, create_pool
from pinecall.memory.reembedding import reembed
from pinecall.providers.embed import embedder_for, model_of
from pinecall.settings import load_settings

PURPOSE: str = "the contacts' facts: reembed, after the box's embedder changed"

# A knowledge base another model wrote is refused until its project pushes it again; a fact has
# no project to push it, so this verb is that push, run once on the hub after the switch.
DONE = "{count} fact(s) embedded again with {model}"


def configure(parser: argparse.ArgumentParser) -> None:
    """One verb: every fact whose vector another model wrote, embedded again."""
    parser.add_argument("verb", choices=("reembed",), help="reembed")
    parser.add_argument(
        "--schema",
        default=DEFAULT_SCHEMA,
        help="the schema the facts are in (a test run owns its own copy)",
    )
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Write them again, and say how many and with what."""
    return asyncio.run(_reembed(arguments.schema))


async def _reembed(schema: str) -> int:
    settings = load_settings()
    pool = await create_pool(settings.database_url, schema=schema)
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            count = await reembed(pool, embedder_for(settings, http))
    finally:
        await pool.close()
    print(DONE.format(count=count, model=model_of(settings)))
    return 0
