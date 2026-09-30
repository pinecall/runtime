"""A recording's own key: made once per call, kept sealed under the vault, erased with the call."""

import base64

from cryptography.fernet import MultiFernet

from pinecall.postgres.pool import Pool
from pinecall.process.sealed_audio import new_key
from pinecall.tenancy.vault import opened, sealed

# The insert is the mint: of two asks at once, or a retry, the first key stands and is the answer.
MINT = """
INSERT INTO recording_keys (call, org, sealed) VALUES (%(call)s, %(org)s, %(sealed)s)
ON CONFLICT (call) DO UPDATE SET call = excluded.call
RETURNING sealed
"""

KEY = "SELECT sealed FROM recording_keys WHERE call = %(call)s"


async def key_for(pool: Pool, vault: MultiFernet, org: str, call: str) -> bytes | None:
    """The key the call's recording is sealed under, made now or before; None if none opens."""
    fresh = sealed(vault, base64.urlsafe_b64encode(new_key()).decode())
    async with pool.connection() as connection:
        row = await (
            await connection.execute(MINT, {"call": call, "org": org, "sealed": fresh})
        ).fetchone()
    return None if row is None else _opened(vault, str(row["sealed"]))


async def key_of(pool: Pool, vault: MultiFernet, call: str) -> bytes | None:
    """The call's recording key; None when it has none, or the vault no longer opens it."""
    async with pool.connection() as connection:
        row = await (await connection.execute(KEY, {"call": call})).fetchone()
    return None if row is None else _opened(vault, str(row["sealed"]))


def _opened(vault: MultiFernet, token: str) -> bytes | None:
    secret = opened(vault, token)
    return base64.urlsafe_b64decode(secret) if isinstance(secret, str) else None
