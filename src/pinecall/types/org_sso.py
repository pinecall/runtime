"""OrgSso: where an org's people prove who they are, and which of them this box lets in."""

import re
from dataclasses import dataclass

from pinecall.types.member import ROLES, Role
from pinecall.types.refusal import DeclarationRefused

# The issuer is an https URL with no query and no fragment, because it is a NAMESPACE and not a
# page: the discovery document hangs off it, every id_token carries it as `iss`, and the two are
# compared as strings. http is refused outright — a token exchange carries a client secret.
_AN_ISSUER = re.compile(r"^https://[^\s?#]+$")

# A domain as an email carries it: at least one dot, nothing but the characters a hostname has.
# What the door matches is the part after the one `@`, folded, so `Nico@TiendaSur.UY` is
# `tiendasur.uy` and a domain written with a capital in the console is the same domain.
_A_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class OrgSso:
    """One org's identity provider: who it is, how this box talks to it, and who it admits."""

    org: str
    # The issuer as the IdP publishes it — `https://accounts.google.com`, an Okta or Entra tenant.
    issuer: str
    client_id: str
    # Sealed under the box's vault key, exactly as a provider key is (orgs/sso.py). It reaches the
    # token endpoint and nothing else: no door of this runtime ever reads one back out.
    client_secret: str
    # The email domains this org signs in with. An address outside them is refused at the callback
    # even when the IdP vouched for it: an Entra tenant can hold guests from anywhere.
    domains: tuple[str, ...]
    # What an email nobody has invited becomes, when it is admitted at all. None is the safe
    # answer and the default: the callback refuses a stranger and an admin invites them.
    role: Role | None = None
    # Whether a password opens this org at all. True is the org saying "the IdP is the only way
    # in"; the break-glass is the operator's, not the org's, because an IdP that stops answering
    # would otherwise lock out the very admin who would turn it off.
    required: bool = False

    def __post_init__(self) -> None:
        if not self.org:
            raise DeclarationRefused("an SSO configuration names the org it is for")
        if not _AN_ISSUER.match(self.issuer):
            raise DeclarationRefused(
                f"an issuer is an https URL with no query, not {self.issuer!r}: "
                "it is what /.well-known/openid-configuration hangs off"
            )
        if self.issuer.endswith("/"):
            raise DeclarationRefused(
                f"an issuer carries no trailing slash: {self.issuer.rstrip('/')!r}, "
                "because every id_token's `iss` is compared to it as a string"
            )
        if not self.client_id.strip() or not self.client_secret.strip():
            raise DeclarationRefused("an SSO configuration carries the client id and its secret")
        if not self.domains:
            raise DeclarationRefused(
                "an SSO configuration names at least one email domain it admits"
            )
        for domain in self.domains:
            if not _A_DOMAIN.match(domain):
                raise DeclarationRefused(f"{domain!r} is not an email domain, folded and bare")
        if self.role is not None and self.role not in ROLES:
            raise DeclarationRefused(f"a role is one of {sorted(ROLES)}, not {self.role!r}")

    def admits(self, email: str) -> bool:
        """Whether this address is in one of the domains the org signs in with."""
        _, _, domain = email.rpartition("@")
        return domain.strip().lower() in self.domains


def a_domain(word: str) -> str:
    """One domain as a row keeps it: trimmed, folded, and without the `@` somebody pasted."""
    return word.strip().lower().lstrip("@")
