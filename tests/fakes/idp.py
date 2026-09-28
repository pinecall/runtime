"""An identity provider that signs id_tokens for the sign-in tests."""

import time
from dataclasses import dataclass, field

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

# One RSA key for the whole suite: making one costs a second.
_IDP_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class IdentityProvider:
    """An OpenID provider on a fake transport: discovery, the token endpoint and its JWKS."""

    issuer: str = "https://idp.test"
    client_id: str = "the-client"
    # What the token endpoint hands back for a code; None refuses the connections.
    id_token: str | None = None
    # How the token endpoint answers a refused code.
    refusal: tuple[int, str] = (400, '{"error": "invalid_grant"}')
    kid: str | None = "k1"
    basic_only: bool = False
    exchanged: list[dict[str, str]] = field(default_factory=list[dict[str, str]])

    def signed(self, *, nonce: str, email: str = "ana@clinica.test", **claims: object) -> str:
        """An id_token this provider signed, with the claims a sign-in needs and any others."""
        now = int(time.time())
        data: dict[str, object] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": "sub-ana",
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "email": email,
            "email_verified": True,
            "name": "Ana García",
            **claims,
        }
        headers = {} if self.kid is None else {"kid": self.kid}
        pem = _IDP_KEY.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(data, pem, algorithm="RS256", headers=headers)

    def transport(self) -> httpx.MockTransport:
        """A transport that answers as this provider does."""
        return httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            methods = ["client_secret_basic"] if self.basic_only else ["client_secret_post"]
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize?prompt=select_account",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                    "token_endpoint_auth_methods_supported": methods,
                },
            )
        if path == "/jwks":
            key: dict[str, object] = RSAAlgorithm.to_jwk(_IDP_KEY.public_key(), as_dict=True)
            if self.kid is not None:
                key["kid"] = self.kid
            return httpx.Response(200, json={"keys": [key]})
        if path == "/token":
            self.exchanged.append(dict(httpx.QueryParams(request.content.decode()).items()))
            if self.id_token is None:
                status, body = self.refusal
                return httpx.Response(status, content=body)
            return httpx.Response(200, json={"id_token": self.id_token})
        return httpx.Response(404)


# A SID made here, never one of Twilio's: two letters and 32 hex digits.
def a_sid(prefix: str, seed: int) -> str:
    """A SID of that kind, the same one for the same seed."""
    return f"{prefix}{seed:032x}"
