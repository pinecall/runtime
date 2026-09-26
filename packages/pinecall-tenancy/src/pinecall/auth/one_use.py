"""A word minted for one use: kept here until it is spent or its minute is up, whichever first."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

# How much of a word is chance: 24 url-safe bytes, 32 characters, which is what a login code, a
# pairing and an OpenID state have always carried.
WORD_BYTES = 24


@dataclass(frozen=True)
class Minted[T]:
    """What a word stands for, and the moment it stops standing for it."""

    value: T
    expires_at: float


# This process's memory and nothing else: every such word is minted by the gateway one request
# talks to and spent against the same process seconds or minutes later. A table would make it
# survive a restart, which is not a property a ten-minute word needs — one use is the property
# that matters, and a dict that pops is exactly that. Expired words go on the next mint or read,
# so the dict never grows with nobody's.
class OneUse[T]:
    """Words that are spent once and die on their own: minted here, read here, gone after."""

    def __init__(self, prefix: str, ttl_s: float, clock: Callable[[], float] = time.time) -> None:
        self._prefix = prefix
        self._ttl_s = ttl_s
        self._clock = clock
        self._minted: dict[str, Minted[T]] = {}

    def a_word(self) -> str:
        """A word nobody has, with the prefix that says what kind of word it is."""
        return f"{self._prefix}{secrets.token_urlsafe(WORD_BYTES)}"

    def expires_from_now(self) -> float:
        """When a word minted now stops being worth anything."""
        return self._clock() + self._ttl_s

    def keep(self, word: str, value: T, expires_at: float) -> None:
        """This word stands for this value until then: what mint does once the value is built."""
        self._forget_the_dead()
        self._minted[word] = Minted(value, expires_at)

    def mint(self, value: T) -> tuple[str, float]:
        """A new word for this value, and when it expires."""
        word, expires_at = self.a_word(), self.expires_from_now()
        self.keep(word, value, expires_at)
        return word, expires_at

    def read(self, word: str) -> Minted[T] | None:
        """What the word stands for, spending nothing. None: unknown, spent already, or expired."""
        self._forget_the_dead()
        return self._minted.get(word)

    def spend(self, word: str) -> T | None:
        """What the word stood for, once. None: unknown, spent already, or expired."""
        self._forget_the_dead()
        minted = self._minted.pop(word, None)
        return None if minted is None else minted.value

    def _forget_the_dead(self) -> None:
        now = self._clock()
        for word in [word for word, minted in self._minted.items() if minted.expires_at <= now]:
            del self._minted[word]
