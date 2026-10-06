"""The proof that a domain an org's SSO admits is the org's: a TXT record the org publishes."""

import asyncio
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.postgres.pool import Pool
from pinecall.process import resolver

# The record's name, as the admin types it into their DNS: `pinecall-verify=<token>` at the domain.
TXT_PREFIX = "pinecall-verify="


PROOF_BYTES = 16


NOT_A_DOMAIN_OF_YOURS = "{domain} is not a domain of this org's SSO: PUT /v1/org/sso names them"


NOT_FOUND_AT = (
    "no TXT record `{txt}` at {domain}: publish it there (what is there now: {found}), then ask "
    "again; a record takes minutes to hours to be seen, by its TTL"
)


ASK = """
INSERT INTO sso_domain_proofs (org, domain, token) VALUES (%(org)s, %(domain)s, %(token)s)
ON CONFLICT (org, domain) DO NOTHING
"""


FORGET = """
DELETE FROM sso_domain_proofs
WHERE org = %(org)s AND NOT (domain = ANY(%(domains)s::text[]))
"""


OF_ORG = """
SELECT org, domain, token, verified_at FROM sso_domain_proofs
WHERE org = %(org)s ORDER BY asked_at, domain
"""


ONE = """
SELECT org, domain, token, verified_at FROM sso_domain_proofs
WHERE org = %(org)s AND domain = %(domain)s
"""


VERIFIED = """
UPDATE sso_domain_proofs SET verified_at = COALESCE(verified_at, now())
WHERE org = %(org)s AND domain = %(domain)s
RETURNING org, domain, token, verified_at
"""


@dataclass(frozen=True)
class Proof:
    """One domain of an org's SSO: the record that proves it, and whether it was seen."""

    org: str
    domain: str
    token: str
    verified_at: datetime | None

    @property
    def txt(self) -> str:
        """The TXT record the org publishes at the domain."""
        return f"{TXT_PREFIX}{self.token}"

    @property
    def verified(self) -> bool:
        """Whether the record was seen at the domain."""
        return self.verified_at is not None


async def proofs_for(pool: Pool, org: str, domains: Sequence[str]) -> list[Proof]:
    """A proof for each domain named, the ones kept standing, the rest forgotten; all of them."""
    params = {"org": org, "domains": list(domains)}
    async with pool.connection() as connection, connection.transaction():
        for domain in domains:
            token = secrets.token_urlsafe(PROOF_BYTES)
            await connection.execute(ASK, {"org": org, "domain": domain, "token": token})
        await connection.execute(FORGET, params)
        rows = await (await connection.execute(OF_ORG, params)).fetchall()
    return [_proof(row) for row in rows]


async def of_org(pool: Pool, org: str) -> list[Proof]:
    """Every domain of the org's SSO with its proof, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_ORG, {"org": org})).fetchall()
    return [_proof(row) for row in rows]


async def verified_domains(pool: Pool, org: str) -> frozenset[str]:
    """The domains of the org's SSO whose record was seen."""
    return frozenset(proof.domain for proof in await of_org(pool, org) if proof.verified)


# Read at the domain itself, where every DNS panel lets a TXT be added; a record once seen stands.
async def verify(pool: Pool, org: str, domain: str) -> Proof:
    """Look for the domain's record now; the proof verified, or refused saying what was found."""
    async with pool.connection() as connection:
        row = await (await connection.execute(ONE, {"org": org, "domain": domain})).fetchone()
    if row is None:
        raise NotFound(NOT_A_DOMAIN_OF_YOURS.format(domain=domain))
    proof = _proof(row)
    if proof.verified:
        return proof
    records = await asyncio.to_thread(resolver.txt_of, domain)
    if proof.txt not in records:
        ours = [record for record in records if record.startswith(TXT_PREFIX)]
        found = ", ".join(f"`{record}`" for record in ours) or "no pinecall-verify record"
        raise DeclarationRefused(NOT_FOUND_AT.format(txt=proof.txt, domain=domain, found=found))
    async with pool.connection() as connection:
        seen = await (await connection.execute(VERIFIED, {"org": org, "domain": domain})).fetchone()
    return _proof(seen) if seen is not None else proof


def _proof(row: DictRow) -> Proof:
    return Proof(
        org=str(row["org"]),
        domain=str(row["domain"]),
        token=str(row["token"]),
        verified_at=row["verified_at"],
    )
