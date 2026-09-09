"""A writer that never stops on its own: the parent kills it mid-append and reads the log back."""

import asyncio
import sys

from pinecall.log.store.postgres import PostgresStore


async def write_until_killed(dsn: str, schema: str, call: str, agent: str) -> None:
    """Append, say the seq out loud, append again. The only way out of this loop is SIGKILL."""
    store = await PostgresStore.connect(dsn, schema=schema, max_size=1)
    while True:
        entry = await store.append(call, agent, "custom", {"name": "tick", "data": {}})
        sys.stdout.write(f"{entry.seq}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(write_until_killed(*sys.argv[1:5]))
