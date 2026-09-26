"""Each org's OpenID provider in Postgres, its client secret sealed under the box's key."""

from __future__ import annotations

from typing import Any

from pinecall.db import Pool
from pinecall.orgs.vault import Cipher, seal, unseal
from pinecall.types import OrgSso, parse_role

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

_DROP = "DELETE FROM org_sso WHERE org = $1 RETURNING org"

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
            seal(self._cipher, sso.client_secret),
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
        return await self._pool.fetchrow(_DROP, org) is not None

    async def with_domain(self, domain: str) -> tuple[OrgSso, ...]:
        """One scan of a table with one row per org: there is nothing here worth an index."""
        rows = await self._pool.fetch(_WITH_DOMAIN, domain)
        return tuple(_a_configuration(self._cipher, row) for row in rows)


def _a_configuration(cipher: Cipher, row: Any) -> OrgSso:
    """One row back into the domain's own OrgSso, the client secret in the clear."""
    role = row["role"]
    return OrgSso(
        org=str(row["org"]),
        issuer=str(row["issuer"]),
        client_id=str(row["client_id"]),
        client_secret=unseal(cipher, str(row["ciphertext"])),
        domains=tuple(str(domain) for domain in row["domains"]),
        role=None if role is None else parse_role(str(role)),
        required=bool(row["required"]),
    )
