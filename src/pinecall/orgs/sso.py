"""The org_sso table: one identity provider per org, its client secret encrypted at rest."""

from __future__ import annotations

from typing import Any, Protocol

from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.orgs.table import DELETED_NOTHING
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, a_cipher
from pinecall.types import OrgSso, a_role


class Sso(Protocol):
    """Where an org's identity provider is kept, replaced whole, and read back with its secret."""

    async def put(self, sso: OrgSso) -> None:
        """Keep this org's provider, replacing whatever it had. One per org."""
        ...

    async def of(self, org: str) -> OrgSso | None:
        """The org's provider with its secret in the clear, or None when it wired none."""
        ...

    async def drop(self, org: str) -> bool:
        """Forget it. False when the org had none: a typo must not read as done."""
        ...

    # What the sign-in page's discovery asks, and the ONE read that spans orgs: which of them
    # sign in with an address of this domain. It answers the configurations whole because the
    # caller needs the org each belongs to; the door hands out the org's name and nothing else.
    async def with_domain(self, domain: str) -> tuple[OrgSso, ...]:
        """Every org that admits this email domain, oldest first."""
        ...


class MemorySso:
    """The table of a clone with no Postgres: the same cipher, forgotten when the process exits."""

    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[OrgSso, str]] = {}

    async def put(self, sso: OrgSso) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[sso.org] = (sso, _sealed(self._cipher, sso.client_secret))

    async def of(self, org: str) -> OrgSso | None:
        """Through the cipher on the way out, exactly as the row below is."""
        row = self._rows.get(org)
        if row is None:
            return None
        kept, ciphertext = row
        return OrgSso(
            org=kept.org,
            issuer=kept.issuer,
            client_id=kept.client_id,
            client_secret=_opened(self._cipher, ciphertext),
            domains=kept.domains,
            role=kept.role,
            required=kept.required,
        )

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None

    async def with_domain(self, domain: str) -> tuple[OrgSso, ...]:
        """In insertion order, which for a dict is the order they were wired."""
        found = [org for org, (kept, _) in self._rows.items() if domain in kept.domains]
        opened = [await self.of(org) for org in found]
        return tuple(one for one in opened if one is not None)


# One row per org, replaced whole: an admin who rotates the client secret sets the whole
# configuration again. There is deliberately no history of a secret in this table.
_PUT = """
INSERT INTO org_sso (org, issuer, client_id, ciphertext, domains, role, required, set_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, now())
    ON CONFLICT (org) DO UPDATE
    SET issuer = excluded.issuer, client_id = excluded.client_id,
        ciphertext = excluded.ciphertext, domains = excluded.domains,
        role = excluded.role, required = excluded.required, set_at = now()
"""

_OF = """
SELECT org, issuer, client_id, ciphertext, domains, role, required FROM org_sso WHERE org = $1
"""

_DROP = "DELETE FROM org_sso WHERE org = $1"

_WITH_DOMAIN = """
SELECT org, issuer, client_id, ciphertext, domains, role, required
  FROM org_sso
 WHERE $1 = ANY (domains)
 ORDER BY set_at, org
"""


class PostgresSso:
    """The table in Postgres, read on every sign-in: what is set now is what the next one uses."""

    def __init__(self, pool: Pool, cipher: Cipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, sso: OrgSso) -> None:
        """The secret reaches this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(
            _PUT,
            sso.org,
            sso.issuer,
            sso.client_id,
            _sealed(self._cipher, sso.client_secret),
            list(sso.domains),
            sso.role,
            sso.required,
        )

    async def of(self, org: str) -> OrgSso | None:
        """One read on the primary key, decrypted for the two doors that talk to the IdP."""
        row = await self._pool.fetchrow(_OF, org)
        return None if row is None else _a_configuration(self._cipher, row)

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        tag = await self._pool.execute(_DROP, org)
        return tag.strip() != DELETED_NOTHING

    async def with_domain(self, domain: str) -> tuple[OrgSso, ...]:
        """One scan of a table with one row per org: there is nothing here worth an index."""
        rows = await self._pool.fetch(_WITH_DOMAIN, domain)
        return tuple(_a_configuration(self._cipher, row) for row in rows)


# None when the box was given no vault key, exactly as the carriers are: the doors that need one
# answer 503 in the vault's own sentence. A box that lost its vault key cannot read a client
# secret at all, so SSO stops working there — and a password opens the org again, which is the
# behaviour a locked-out admin wants and the one an operator has to know about.
def sso_for(settings: Settings, pool: Pool | None) -> Sso | None:
    """Postgres when the process opened one, memory with none, nothing with no vault key."""
    if not settings.vault_key:
        return None
    cipher = a_cipher(settings.vault_key)
    return MemorySso(cipher) if pool is None else PostgresSso(pool, cipher)


def _a_configuration(cipher: Cipher, row: Any) -> OrgSso:
    """One row back into the domain's own OrgSso, the client secret in the clear."""
    role = row["role"]
    return OrgSso(
        org=str(row["org"]),
        issuer=str(row["issuer"]),
        client_id=str(row["client_id"]),
        client_secret=_opened(cipher, str(row["ciphertext"])),
        domains=tuple(str(domain) for domain in row["domains"]),
        role=None if role is None else a_role(str(role)),
        required=bool(row["required"]),
    )


def _sealed(cipher: Cipher, secret: str) -> str:
    """The client secret as a row keeps it: a Fernet token, never the secret itself."""
    return cipher.encrypt(secret.encode()).decode()


def _opened(cipher: Cipher, ciphertext: str) -> str:
    """One row back into the secret the token endpoint takes."""
    return cipher.decrypt(ciphertext.encode()).decode()


__all__ = ["NO_VAULT_KEY", "MemorySso", "NoVaultKey", "PostgresSso", "Sso", "sso_for"]
