"""A connection pool as its callers use it, opened by the one module that may name the driver."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from pinecall.log.store.postgres import DEFAULT_SCHEMA, create_pool


# The gateway reads its API keys and its routes off tables the log knows nothing about, and both
# read them through this: a pool as its callers use it, and nothing of the driver underneath. One
# Protocol, because two minimal views of the same object drift apart on the day somebody widens one.
class Pool(Protocol):
    """One row, many rows, a statement, and a way to give the pool back."""

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None: ...

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]: ...

    async def execute(self, query: str, /, *args: Any) -> str: ...

    async def close(self) -> None: ...


async def open_pool(database_url: str, *, schema: str = DEFAULT_SCHEMA) -> Pool:
    """The pool the gateway holds for its whole life. The lifespan that opened it closes it."""
    return cast(Pool, await create_pool(database_url, schema=schema))
