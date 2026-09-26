"""Pairing: a word a terminal prints, a browser fills with a key, and the terminal collects once."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from pinecall.auth.one_use import OneUse

# `pinecall login` prints `https://<gateway>/cli?c=<code>` and opens it. The code goes in the URL
# and the key never does — a URL is in a shell history, a browser history and a proxy log. Ten
# minutes, because between printing it and collecting anything a person has to open a browser,
# read a card and type a password; five was the login code's budget and a login code is spent by
# a page that is already open.
CODE_PREFIX = "cli_"
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
    # What the terminal called itself, so the card a person approves says what it is signing in.
    device: str | None = None
    # What the browser put there. None until somebody approves, and the pairing is spent the
    # moment the terminal takes it.
    key: str | None = None
    org: str | None = None


# A pairing is opened by the gateway a `pinecall login` is talking to and filled by a browser
# against the same process, a minute apart: a one-use word (auth/one_use.py), spent by the
# terminal that collects the key and by nobody before.
class Pairings:
    """The pairings opened here: one key each, one collection each, and a life of ten minutes."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._words: OneUse[_Opened] = OneUse(CODE_PREFIX, CODE_TTL_S, clock)

    def open(self, device: str | None = None) -> Pairing:
        """A word for a terminal to print, good for ten minutes or until its key is collected."""
        code, expires_at = self._words.mint(_Opened(device))
        return Pairing(code=code, expires_at=expires_at)

    def asking(self, code: str) -> Asked | None:
        """What the browser is being shown: which terminal, and whether it is already answered.

        It spends nothing. A person approving has to see WHAT they are approving, and the door
        that hands the key over is the terminal's — reading it from a browser would take the key
        away from the process that asked for it.
        """
        minted = self._words.read(code)
        if minted is None:
            return None
        return Asked(
            device=minted.value.device,
            expires_at=minted.expires_at,
            answered=minted.value.key is not None,
        )

    def fill(self, code: str, key: str, org: str) -> bool:
        """The browser's answer. False for a word unknown, expired, or already answered."""
        minted = self._words.read(code)
        if minted is None or minted.value.key is not None:
            return False
        minted.value.key, minted.value.org = key, org
        return True

    def collect(self, code: str) -> Collected:
        """The key the browser left, once. Waiting while it has left none; gone once taken."""
        minted = self._words.read(code)
        if minted is None:
            return Collected(key=None, waiting=False)
        if minted.value.key is None:
            return Collected(key=None, waiting=True)
        self._words.spend(code)
        return Collected(key=minted.value.key, waiting=False)
