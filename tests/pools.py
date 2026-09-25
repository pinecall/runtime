"""A fake pool's connection: the pool itself, held, with a transaction that changes nothing."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Any, Protocol


class Statements(Protocol):
    """What a fake pool answers: the three verbs, and nothing of a driver."""

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None: ...

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]: ...

    async def execute(self, query: str, /, *args: Any) -> str: ...


# A connection off a fake pool is the pool again: every statement lands where the pool's would,
# a transaction is nothing to a dict, and executemany is the statement once per row — so a test
# that counts what was written sees the same rows whichever way the code sent them.
class Held:
    """The fake pool, held as a connection."""

    def __init__(self, pool: Statements) -> None:
        self._pool = pool

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None:
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]:
        return await self._pool.fetch(query, *args)

    async def execute(self, query: str, /, *args: Any) -> str:
        return await self._pool.execute(query, *args)

    async def executemany(self, query: str, args: Iterable[Sequence[Any]], /) -> None:
        for row in args:
            await self._pool.execute(query, *row)

    def transaction(self) -> AbstractAsyncContextManager[None]:
        return nullcontext()


def acquired(pool: Statements) -> AbstractAsyncContextManager[Held]:
    """What a fake pool's `acquire()` answers: itself, held."""

    @asynccontextmanager
    async def held() -> AsyncGenerator[Held]:
        yield Held(pool)

    return held()
