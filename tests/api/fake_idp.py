"""An identity provider, scripted: a real JWKS and real signed id_tokens, over no network."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://idp.test"
KID = "the-one-key"
CLIENT_ID = "the-gateway-at-the-idp"
CLIENT_SECRET = "a-client-secret-nobody-will-ever-deploy"

# One key pair for the whole suite: generating 2048 bits per test is a tenth of a second each,
# and what is under test is the checking, not the generating. It never leaves this process, and
# the cache is a dict rather than a global so nothing here reads as a constant being reassigned.
_kept: dict[str, rsa.RSAPrivateKey] = {}


def _key() -> rsa.RSAPrivateKey:
    """The provider's signing key, made once and kept for the run."""
    if "the key" not in _kept:
        _kept["the key"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _kept["the key"]


@dataclass
class FakeIdp:
    """A provider that answers the three doors a sign-in asks, and signs what it vouches for."""

    issuer: str = ISSUER
    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    # What the next id_token says. A test that wants a refusal moves one of these.
    said: dict[str, Any] = field(default_factory=dict[str, Any])
    # The PKCE challenge the authorization request carried, so the exchange proves the verifier
    # against it exactly as a real provider does.
    challenge: str | None = None
    # What the provider refuses the exchange with, when a test wants that afternoon.
    refuses: str | None = None
    # Every client secret the exchange arrived with: a test reads it to prove the vault's round
    # trip, and nothing else in this suite ever sees one.
    secrets_seen: list[str] = field(default_factory=list[str])

    def transport(self) -> httpx.MockTransport:
        """An httpx transport that answers this provider and 404s everything else."""
        return httpx.MockTransport(self._answer)

    def vouches_for(self, nonce: str, email: str = "nico@tiendasur.uy", **overrides: Any) -> None:
        """What the next id_token claims about the person coming back."""
        now = int(time.time())
        self.said = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": "idp-subject-1",
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "email": email,
            "email_verified": True,
            "name": "Nico",
            **overrides,
        }

    def id_token(self) -> str:
        """The claims above, signed with the key the JWKS publishes."""
        return jwt.encode(self.said, _key(), algorithm="RS256", headers={"kid": KID})

    def _answer(self, request: httpx.Request) -> httpx.Response:
        """The three doors, and a 404 for anything a sign-in has no business asking."""
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=self._configuration())
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [_a_public_jwk()]})
        if path == "/token":
            return self._exchanged(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _configuration(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
        }

    def _exchanged(self, request: httpx.Request) -> httpx.Response:
        """The code spent, once the secret and the PKCE verifier are the ones we were given."""
        if self.refuses is not None:
            return httpx.Response(400, json={"error": self.refuses})
        form = dict(httpx.QueryParams(request.content.decode()))
        self.secrets_seen.append(form.get("client_secret", ""))
        if form.get("client_secret") != self.client_secret:
            return httpx.Response(401, json={"error": "invalid_client"})
        if (
            self.challenge is not None
            and a_challenge(form.get("code_verifier", "")) != self.challenge
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"id_token": self.id_token(), "token_type": "Bearer"})


def a_challenge(verifier: str) -> str:
    """The PKCE challenge for a verifier: the same sha256 the gateway sends, spelled here too."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _a_public_jwk() -> dict[str, Any]:
    """The signing key's public half, as a JWKS publishes it."""
    published: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(_key().public_key()))
    return {**published, "kid": KID, "use": "sig", "alg": "RS256"}
