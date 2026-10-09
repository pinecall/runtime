"""An org's telemetry row: the endpoint in clear, the headers sealed in the vault, read per call."""

from dataclasses import dataclass

from cryptography.fernet import MultiFernet

from pinecall.domain.telemetry import Telemetry
from pinecall.postgres.pool import Pool
from pinecall.tenancy.vault import opened, sealed

ONE = "SELECT endpoint, ciphertext, pii FROM org_telemetry WHERE org = %(org)s"
PUT = """
INSERT INTO org_telemetry (org, endpoint, ciphertext, pii)
VALUES (%(org)s, %(endpoint)s, %(ciphertext)s, %(pii)s)
ON CONFLICT (org) DO UPDATE SET
    endpoint = excluded.endpoint, ciphertext = excluded.ciphertext, pii = excluded.pii,
    set_at = now()
"""
DROP = "DELETE FROM org_telemetry WHERE org = %(org)s RETURNING org"


@dataclass(frozen=True)
class Described:
    """The row as the org may read it back: the headers by name, never by value."""

    endpoint: str
    header_names: tuple[str, ...]
    pii: bool


async def put_telemetry(pool: Pool, vault: MultiFernet, org: str, telemetry: Telemetry) -> None:
    """Keep where the org's traces go, replacing what it had."""
    ciphertext = sealed(vault, dict(telemetry.headers)) if telemetry.headers else None
    values = {
        "org": org,
        "endpoint": telemetry.endpoint,
        "ciphertext": ciphertext,
        "pii": telemetry.pii,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, values)


async def telemetry_of(pool: Pool, vault: MultiFernet, org: str) -> Telemetry | None:
    """Where the org's traces go, headers opened; None when it sends them nowhere."""
    async with pool.connection() as connection:
        row = await (await connection.execute(ONE, {"org": org})).fetchone()
    if row is None:
        return None
    headers = opened(vault, str(row["ciphertext"])) if row["ciphertext"] else {}
    if not isinstance(headers, dict):
        return None
    return Telemetry(
        endpoint=str(row["endpoint"]),
        headers={name: str(value) for name, value in headers.items()},
        pii=bool(row["pii"]),
    )


async def described(pool: Pool, vault: MultiFernet, org: str) -> Described | None:
    """The row for the org to read: the endpoint, which headers are set, and PII."""
    telemetry = await telemetry_of(pool, vault, org)
    if telemetry is None:
        return None
    return Described(telemetry.endpoint, tuple(telemetry.headers), telemetry.pii)


async def drop_telemetry(pool: Pool, org: str) -> bool:
    """Forget where the org's traces went; whether it sent them anywhere."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP, {"org": org})
        return await dropped.fetchone() is not None
