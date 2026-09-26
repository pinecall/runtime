"""The sign-ins in flight: the state a browser carries to the IdP and brings back, once."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from pinecall.auth.one_use import OneUse
from pinecall.auth.openid import a_verifier

# What travels in the URL is this word and nothing else: the org, the nonce and the PKCE verifier
# stay here. A state that carried them would be a state somebody could write themselves.
STATE_PREFIX = "st_"
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
    # Which BOX-WIDE provider this sign-in went out to (orgs/signin.py), when it is not an org's
    # own: `org` is then nobody's, and only that provider's callback may spend the state.
    provider: str | None = None


# A sign-in is one person between two requests seconds apart, and a gateway that restarted in
# the middle of one is a person pressing the button again: a one-use word (auth/one_use.py).
class Handshakes:
    """The sign-ins started here and not yet finished. One use each; expired ones are as good."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._words: OneUse[Handshake] = OneUse(STATE_PREFIX, STATE_TTL_S, clock)

    def open(
        self,
        org: str,
        redirect_uri: str,
        pairing: str | None = None,
        provider: str | None = None,
    ) -> Handshake:
        """A state, a nonce and a verifier for one sign-in, good for ten minutes."""
        handshake = Handshake(
            state=self._words.a_word(),
            org=org,
            nonce=secrets.token_urlsafe(NONCE_BYTES),
            verifier=a_verifier(),
            redirect_uri=redirect_uri,
            pairing=pairing,
            expires_at=self._words.expires_from_now(),
            provider=provider,
        )
        self._words.keep(handshake.state, handshake, handshake.expires_at)
        return handshake

    def spend(self, state: str) -> Handshake | None:
        """The sign-in this state stands for, once. None when it is unknown, spent or expired."""
        return self._words.spend(state)
