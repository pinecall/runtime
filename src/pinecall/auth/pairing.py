"""Pairing: a word a terminal prints, a browser fills with a key, and the terminal collects once."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

# `pinecall login` prints `https://<gateway>/cli?c=<code>` and opens it. The code goes in the URL
# and the key never does — a URL is in a shell history, a browser history and a proxy log. Ten
# minutes, because between printing it and collecting anything a person has to open a browser,
# read a card and type a password; five was the login code's budget and a login code is spent by
# a page that is already open.
CODE_PREFIX = "cli_"
CODE_BYTES = 24
CODE_TTL_S = 600.0


@dataclass(frozen=True)
class Pairing:
    """A pairing at the one moment it is worth something: the word, and when it stops being."""

    code: str
    expires_at: float


@dataclass(frozen=True)
class Asked:
    """A pairing as the browser about to approve it sees it. No key is in this, ever."""

    device: str | None
    expires_at: float
    answered: bool


@dataclass(frozen=True)
class Collected:
    """What the terminal found when it asked: the key once it is there, or why there is none."""

    key: str | None
    # True while the browser has not answered yet, so a terminal knows to keep asking rather than
    # to give up. False with no key means the word is gone: expired, collected, or never minted.
    waiting: bool


@dataclass
class _Opened:
    expires_at: float
    # What the terminal called itself, so the card a person approves says what it is signing in.
    device: str | None = None
    # What the browser put there. None until somebody approves, and the pairing is spent the
    # moment the terminal takes it.
    key: str | None = None
    org: str | None = None


# This process's memory and nothing else, as the login codes are: a pairing is opened by the
# gateway a `pinecall login` is talking to and filled by a browser against the same process,
# a minute apart. A table would make it survive a restart, which a ten-minute word does not need.
class Pairings:
    """The pairings opened here: one key each, one collection each, and a life of ten minutes."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._opened: dict[str, _Opened] = {}

    def open(self, device: str | None = None) -> Pairing:
        """A word for a terminal to print, good for ten minutes or until its key is collected."""
        self._forget_the_dead()
        code = f"{CODE_PREFIX}{secrets.token_urlsafe(CODE_BYTES)}"
        expires_at = self._clock() + CODE_TTL_S
        self._opened[code] = _Opened(expires_at, device)
        return Pairing(code=code, expires_at=expires_at)

    def asking(self, code: str) -> Asked | None:
        """What the browser is being shown: which terminal, and whether it is already answered.

        It spends nothing. A person approving has to see WHAT they are approving, and the door
        that hands the key over is the terminal's — reading it from a browser would take the key
        away from the process that asked for it.
        """
        self._forget_the_dead()
        opened = self._opened.get(code)
        if opened is None:
            return None
        return Asked(
            device=opened.device, expires_at=opened.expires_at, answered=opened.key is not None
        )

    def fill(self, code: str, key: str, org: str) -> bool:
        """The browser's answer. False for a word unknown, expired, or already answered."""
        self._forget_the_dead()
        opened = self._opened.get(code)
        if opened is None or opened.key is not None:
            return False
        opened.key, opened.org = key, org
        return True

    def collect(self, code: str) -> Collected:
        """The key the browser left, once. Waiting while it has left none; gone once taken."""
        self._forget_the_dead()
        opened = self._opened.get(code)
        if opened is None:
            return Collected(key=None, waiting=False)
        if opened.key is None:
            return Collected(key=None, waiting=True)
        del self._opened[code]
        return Collected(key=opened.key, waiting=False)

    def _forget_the_dead(self) -> None:
        """Expired words go on the next open, fill or collect, so the dict holds only live ones."""
        now = self._clock()
        for code in [code for code, opened in self._opened.items() if opened.expires_at <= now]:
            del self._opened[code]
