"""Login codes: a one-use word a key holder mints so a browser logs in with no key in a URL."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from pinecall.auth.keys import KeyRecord

# `pinecall run` prints `https://<gateway>/a/<agent>?login=<code>`: the code goes in the URL, the
# key never does. A URL is in a shell history, a browser history and a proxy log; a code that dies
# in five minutes and on first use is nothing to find there.
CODE_PREFIX = "lc_"
CODE_BYTES = 24
CODE_TTL_S = 300.0

# The id a record carries when it stands for a key that has not been minted. A code is normally
# minted FROM a live key, but the sign-in at an org's identity provider mints one for a person
# who holds none yet (api/login_sso.py): the browser spending the code is what mints their key,
# and spending reads the org, the world, the scopes and the person off the record — never its id.
NO_KEY_YET = "k_none"


@dataclass(frozen=True)
class Code:
    """A code at the one moment it is worth something: the word, and when it stops being."""

    code: str
    expires_at: float


@dataclass(frozen=True)
class _Minted:
    record: KeyRecord
    expires_at: float


# This process's memory and nothing else: a code is minted by the gateway a `pinecall run` talks
# to and spent by the browser that URL opens, seconds apart, against the same process. A table
# would make it survive a restart, which is not a property a five-minute word needs.
class LoginCodes:
    """The codes minted here and not yet spent. One use each; expired ones are as good as spent."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._minted: dict[str, _Minted] = {}

    def mint(self, record: KeyRecord) -> Code:
        """A code that stands for this key's record for five minutes, or until it is spent."""
        self._forget_the_dead()
        code = f"{CODE_PREFIX}{secrets.token_urlsafe(CODE_BYTES)}"
        expires_at = self._clock() + CODE_TTL_S
        self._minted[code] = _Minted(record, expires_at)
        return Code(code=code, expires_at=expires_at)

    def spend(self, code: str) -> KeyRecord | None:
        """The record behind the code, once. None for a code unknown, spent already, or expired."""
        self._forget_the_dead()
        minted = self._minted.pop(code, None)
        return None if minted is None else minted.record

    def _forget_the_dead(self) -> None:
        """Expired codes go on the next mint or spend, so the dict never grows with nobody's."""
        now = self._clock()
        for code in [code for code, minted in self._minted.items() if minted.expires_at <= now]:
            del self._minted[code]
