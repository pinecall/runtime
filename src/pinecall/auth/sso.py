"""The sign-ins in flight: the state a browser carries to the IdP and brings back, once."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from pinecall.auth.openid import a_verifier

# What travels in the URL is this word and nothing else: the org, the nonce and the PKCE verifier
# stay here. A state that carried them would be a state somebody could write themselves.
STATE_PREFIX = "st_"
STATE_BYTES = 24
NONCE_BYTES = 24

# How long a person has between the redirect out and the callback back. Ten minutes is a person
# typing a password, answering a second factor and reading a consent screen, and it is what the
# terminal pairings already allow.
STATE_TTL_S = 600.0


@dataclass(frozen=True)
class Handshake:
    """One sign-in between the redirect and the callback: whose, with what, and where it ends."""

    state: str
    org: str
    nonce: str
    # PKCE: the challenge went to the IdP, this is what the exchange proves it against.
    verifier: str
    # Exactly the string the authorization request carried, because the token endpoint compares
    # the two: a callback that rebuilt it would break the day a box was reached by another name.
    redirect_uri: str
    # The terminal that sent the person here, when one did: `pinecall login` prints /cli?c=<code>,
    # the browser has no key, and the console sends them through SSO first. The word rides along
    # so the browser lands back on that card and approves the terminal as it always did.
    pairing: str | None
    expires_at: float


# This process's memory, exactly as the login codes and the pairings are: a sign-in is one person
# between two requests seconds apart, and a gateway that restarted in the middle of one is a
# person pressing the button again. A table would make a ten-minute word survive a restart, which
# is not a property it needs — and it is one-use here, which is the property that matters.
class Handshakes:
    """The sign-ins started here and not yet finished. One use each; expired ones are as good."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._open: dict[str, Handshake] = {}

    def open(self, org: str, redirect_uri: str, pairing: str | None = None) -> Handshake:
        """A state, a nonce and a verifier for one sign-in, good for ten minutes."""
        self._forget_the_dead()
        handshake = Handshake(
            state=f"{STATE_PREFIX}{secrets.token_urlsafe(STATE_BYTES)}",
            org=org,
            nonce=secrets.token_urlsafe(NONCE_BYTES),
            verifier=a_verifier(),
            redirect_uri=redirect_uri,
            pairing=pairing,
            expires_at=self._clock() + STATE_TTL_S,
        )
        self._open[handshake.state] = handshake
        return handshake

    def spend(self, state: str) -> Handshake | None:
        """The sign-in this state stands for, once. None when it is unknown, spent or expired."""
        self._forget_the_dead()
        return self._open.pop(state, None)

    def _forget_the_dead(self) -> None:
        """Expired states go on the next open or spend, so the dict never grows with nobody's."""
        now = self._clock()
        for state in [one for one, handshake in self._open.items() if handshake.expires_at <= now]:
            del self._open[state]
