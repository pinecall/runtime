"""Login codes: a one-use word a key holder mints so a browser logs in with no key in a URL."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from pinecall.auth.keys import KeyRecord
from pinecall.auth.one_use import OneUse

# `pinecall start` prints `https://<gateway>/a/<agent>?login=<code>`: the code goes in the URL, the
# key never does. A URL is in a shell history, a browser history and a proxy log; a code that dies
# in five minutes and on first use is nothing to find there.
CODE_PREFIX = "lc_"
CODE_TTL_S = 300.0

# The id a record carries when it stands for a key that has not been minted. A code is normally
# minted FROM a live key, but the sign-in at an org's identity provider mints one for a person who
# holds none yet (api/accounts/sso_login.py): the browser spending the code is what mints their key,
# and spending reads the org, the world, the scopes and the person off the record — never its id.
NO_KEY_YET = "k_none"


@dataclass(frozen=True)
class Code:
    """A code at the one moment it is worth something: the word, and when it stops being."""

    code: str
    expires_at: float


# A code is minted by the gateway a `pinecall start` talks to and spent by the browser that URL
# opens, seconds apart, against the same process: a one-use word (auth/one_use.py).
class LoginCodes:
    """The codes minted here and not yet spent. One use each; expired ones are as good as spent."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._words: OneUse[KeyRecord] = OneUse(CODE_PREFIX, CODE_TTL_S, clock)

    def mint(self, record: KeyRecord) -> Code:
        """A code that stands for this key's record for five minutes, or until it is spent."""
        code, expires_at = self._words.mint(record)
        return Code(code=code, expires_at=expires_at)

    def spend(self, code: str) -> KeyRecord | None:
        """The record behind the code, once. None for a code unknown, spent already, or expired."""
        return self._words.spend(code)
