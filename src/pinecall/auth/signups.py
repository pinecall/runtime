"""Pending sign-ups: an org asked for and not yet made, until its email proves it with a code."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

# Six digits a person copies from a letter into a page. A million of them, six tries each and a
# quarter of an hour to make them: a guess is a one-in-166 666 chance per code, and a code that
# is burned has to be asked for again, by mail, to the address being proved.
CODE_DIGITS = 6
CODE_TTL_S = 15 * 60.0
ATTEMPTS = 6

type Refusal = Literal["wrong", "expired", "burned"]


@dataclass(frozen=True)
class Pending:
    """What the person asked for, kept until the code proves the address: never the code itself."""

    email: str
    slug: str
    name: str | None
    person: str
    # The password as passwords.hashed made it: the clear one is never held, here or anywhere.
    hashed: str
    device: str | None
    code_hash: bytes
    salt: bytes
    expires_at: float
    attempts: int = 0


@dataclass(frozen=True)
class NotVerified:
    """Why a code was not taken: wrong, expired, or burned by too many wrong ones."""

    reason: Refusal


# This process's memory, as the login codes are (auth/codes.py): an org nobody has proved the
# address of is not a row anywhere, so a fake email leaves nothing standing, and a deploy in the
# fifteen minutes costs the person a new code — the resend door — and nothing else.
class PendingSignups:
    """The sign-ups asked for here and not yet verified, one per email."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._pending: dict[str, Pending] = {}

    # A second sign-up from the same address replaces the first: the newest code is the only one
    # that works, whether it came from a resend or from somebody filling the form again.
    def begin(
        self,
        email: str,
        slug: str,
        name: str | None,
        person: str,
        hashed: str,
        device: str | None,
    ) -> tuple[Pending, str]:
        """The pending row and its code in clear — once, for the letter, and kept nowhere."""
        self._forget_the_dead()
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        salt = secrets.token_bytes(16)
        pending = Pending(
            email=email,
            slug=slug,
            name=name,
            person=person,
            hashed=hashed,
            device=device,
            code_hash=_hashed(salt, code),
            salt=salt,
            expires_at=self._clock() + CODE_TTL_S,
        )
        self._pending[email] = pending
        return pending, code

    def renewed(self, email: str) -> tuple[Pending, str] | None:
        """The same sign-up with a new code and fifteen more minutes; None when there is none."""
        self._forget_the_dead()
        asked = self._pending.get(email)
        if asked is None:
            return None
        return self.begin(
            asked.email, asked.slug, asked.name, asked.person, asked.hashed, asked.device
        )

    def verify(self, email: str, code: str) -> Pending | NotVerified:
        """The sign-up, taken out once, when the code is its own; why not, otherwise."""
        asked = self._pending.get(email)
        if asked is None:
            return NotVerified("wrong")
        if asked.expires_at <= self._clock():
            del self._pending[email]
            return NotVerified("expired")
        if asked.attempts >= ATTEMPTS:
            return NotVerified("burned")
        if not hmac.compare_digest(asked.code_hash, _hashed(asked.salt, code)):
            self._pending[email] = replace(asked, attempts=asked.attempts + 1)
            return NotVerified("burned" if asked.attempts + 1 >= ATTEMPTS else "wrong")
        del self._pending[email]
        return asked

    def _forget_the_dead(self) -> None:
        """Expired sign-ups go on the next begin, so the dict never grows with nobody's."""
        now = self._clock()
        for email in [email for email, one in self._pending.items() if one.expires_at <= now]:
            del self._pending[email]


def _hashed(salt: bytes, code: str) -> bytes:
    """What is kept of a code: a salted digest, so the row read out of memory proves nothing."""
    return hashlib.sha256(salt + code.encode()).digest()
