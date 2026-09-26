"""A connection pool as its callers use it, opened by the one module that may name the driver."""

from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


# One connection, held for the length of a transaction. Every statement a caller runs on the pool
# itself is its own transaction; a write that is several statements — spend a link then seat the
# member, end a fact then write its replacement — takes a connection and runs them inside one, or
# a failure between two leaves the table saying half of it (2026-09-26).
class Connection(Protocol):
    """A pool's connection while a caller holds it: the same verbs, plus a transaction."""

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None: ...

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]: ...

    async def execute(self, query: str, /, *args: Any) -> str: ...

    async def executemany(self, query: str, args: Iterable[Sequence[Any]], /) -> None: ...

    def transaction(self) -> AbstractAsyncContextManager[Any]: ...


# The gateway reads its API keys and its routes off tables the log knows nothing about, and both
# read them through this: a pool as its callers use it, and nothing of the driver underneath. One
# Protocol, because two minimal views of the same object drift apart on the day somebody widens one.
class Pool(Protocol):
    """One row, many rows, a statement, a connection to hold, and a way to give the pool back."""

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None: ...

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]: ...

    async def execute(self, query: str, /, *args: Any) -> str: ...

    def acquire(self) -> AbstractAsyncContextManager[Connection]: ...

    async def close(self) -> None: ...
