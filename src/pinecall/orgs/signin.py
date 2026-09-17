"""The box-wide identity providers: which are known, and the one the operator wired — Google."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.orgs.box import SIGN_IN, BoxSettings

# The providers a box may offer to EVERY org's people at its sign-in page, as against the one an
# org wires for its own (orgs/sso.py). Each is an OpenID Connect issuer this runtime already
# knows how to talk to (auth/openid.py); what the operator brings is a client at it. One entry
# here and one row in box_settings is a second provider — nothing else is written per provider.
GOOGLE = "google"


@dataclass(frozen=True)
class Known:
    """One provider this runtime can offer box-wide: its issuer, as it publishes it."""

    issuer: str


PROVIDERS: dict[str, Known] = {GOOGLE: Known(issuer="https://accounts.google.com")}


@dataclass(frozen=True)
class BoxProvider:
    """A provider the operator wired: the client this gateway is at it, and its secret."""

    name: str
    issuer: str
    client_id: str
    client_secret: str


class BoxSignIn:
    """The wired providers, over the box's settings: one row each, the secret sealed."""

    def __init__(self, box: BoxSettings) -> None:
        self._box = box

    async def of(self, name: str) -> BoxProvider | None:
        """The provider as wired, secret in the clear; None when unwired, unknown, or sealed
        under a vault key this box no longer holds — which is unwired, to every caller."""
        known = PROVIDERS.get(name)
        kept = None if known is None else await self._box.of(SIGN_IN.format(provider=name))
        if known is None or kept is None or kept.secret is None:
            return None
        return BoxProvider(
            name=name,
            issuer=known.issuer,
            client_id=str(kept.value.get("client_id") or ""),
            client_secret=kept.secret,
        )

    async def put(self, name: str, client_id: str, client_secret: str) -> None:
        """Keep the client for this provider, replacing what it had. NoVaultKey with no key."""
        await self._box.put(SIGN_IN.format(provider=name), {"client_id": client_id}, client_secret)

    async def drop(self, name: str) -> bool:
        """Forget it. False when nothing was wired: a typo must not read as done."""
        return await self._box.drop(SIGN_IN.format(provider=name))
