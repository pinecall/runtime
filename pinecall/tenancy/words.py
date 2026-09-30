"""Words spent once, kept in Postgres: any gateway mints one, any gateway spends it."""

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.fernet import MultiFernet
from pydantic import TypeAdapter

from pinecall.postgres.pool import Pool

WORD_BYTES = 24

# The word is kept as its hash and the value sealed: a copy of the table opens nothing.
KEPT = """
insert into one_use_words (word_hash, kind, sealed, expires_at)
values (%(hash)s, %(kind)s, %(sealed)s, %(expires_at)s)
on conflict (word_hash) do update
set kind = excluded.kind, sealed = excluded.sealed, expires_at = excluded.expires_at, attempts = 0
"""

READ = "select sealed, expires_at, attempts from one_use_words where word_hash = %(hash)s"

# Spent once: of two gateways spending one word, one gets the value.
SPENT = """
delete from one_use_words where word_hash = %(hash)s and expires_at > %(now)s
returning sealed, expires_at
"""

# The value changed in place, its end kept: a terminal given its key.
FILLED = """
update one_use_words set sealed = %(sealed)s
where word_hash = %(hash)s and expires_at > %(now)s
returning word_hash
"""

TRIED = """
update one_use_words set attempts = attempts + 1 where word_hash = %(hash)s returning attempts
"""

FORGOTTEN = "delete from one_use_words where word_hash = %(hash)s"

SWEPT = "delete from one_use_words where expires_at <= %(now)s"


@dataclass(frozen=True)
class Words:
    """Where words are kept, the vault their values are sealed under, and the clock they die by."""

    pool: Pool
    vault: MultiFernet
    clock: Callable[[], float] = time.time


@dataclass(frozen=True)
class OneUseValue[T]:
    """A value kept under a one-use word, until when, and how often a guess at it failed."""

    value: T
    expires_at: float
    attempts: int = 0


class OneUse[T]:
    """Words of one kind spent once, each dying on its own."""

    def __init__(self, kind: tuple[str, float], adapter: TypeAdapter[T], words: Words) -> None:
        """The kind is the words' prefix and how long they live; the adapter seals their values."""
        self.prefix, self.ttl_s = kind
        self.adapter = adapter
        self.words = words

    def word(self) -> str:
        """A new word, not yet holding anything."""
        return f"{self.prefix}{secrets.token_urlsafe(WORD_BYTES)}"

    async def keep(self, word: str, value: T) -> float:
        """Hold the value under the word until it dies, and say when."""
        now = self.words.clock()
        expires_at = now + self.ttl_s
        row = {
            "hash": _hashed(word),
            "kind": self.prefix,
            "sealed": self._sealed(value),
            "expires_at": expires_at,
        }
        async with self.words.pool.connection() as connection, connection.transaction():
            await connection.execute(SWEPT, {"now": now})
            await connection.execute(KEPT, row)
        return expires_at

    async def mint(self, value: T) -> tuple[str, float]:
        """A new word holding the value, and when it dies."""
        word = self.word()
        return word, await self.keep(word, value)

    async def read(self, word: str) -> OneUseValue[T] | None:
        """What the word holds, dead or not, without spending it."""
        async with self.words.pool.connection() as connection:
            row = await (await connection.execute(READ, {"hash": _hashed(word)})).fetchone()
        if row is None:
            return None
        return OneUseValue(self._opened(row["sealed"]), row["expires_at"], row["attempts"])

    async def alive(self, word: str) -> OneUseValue[T] | None:
        """What the word holds while it lives, without spending it."""
        found = await self.read(word)
        return None if found is None or found.expires_at <= self.words.clock() else found

    async def spend(self, word: str) -> T | None:
        """What the word holds, spent: it opens nothing after."""
        wanted = {"hash": _hashed(word), "now": self.words.clock()}
        async with self.words.pool.connection() as connection:
            row = await (await connection.execute(SPENT, wanted)).fetchone()
        return None if row is None else self._opened(row["sealed"])

    async def fill(self, word: str, value: T) -> bool:
        """Change what a living word holds, its end kept; False for a dead one."""
        wanted = {"hash": _hashed(word), "now": self.words.clock(), "sealed": self._sealed(value)}
        async with self.words.pool.connection() as connection:
            return await (await connection.execute(FILLED, wanted)).fetchone() is not None

    async def tried(self, word: str) -> int:
        """Count one more wrong guess at the word, and say how many there were."""
        async with self.words.pool.connection() as connection:
            row = await (await connection.execute(TRIED, {"hash": _hashed(word)})).fetchone()
        return 0 if row is None else int(row["attempts"])

    async def forget(self, word: str) -> None:
        """The word opens nothing from now on."""
        async with self.words.pool.connection() as connection:
            await connection.execute(FORGOTTEN, {"hash": _hashed(word)})

    def _sealed(self, value: T) -> str:
        return self.words.vault.encrypt(self.adapter.dump_json(value)).decode()

    def _opened(self, sealed: str) -> T:
        return self.adapter.validate_json(self.words.vault.decrypt(sealed.encode()))


def _hashed(word: str) -> bytes:
    return hashlib.sha256(word.encode()).digest()
