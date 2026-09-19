"""The api_keys table in Postgres: a key found by its hash, issued, listed, revoked, touched."""

from __future__ import annotations

from typing import Any

from pinecall.auth.keys import (
    Issued,
    KeyRecord,
    ListedKey,
    a_key_id,
    fingerprint,
    mint,
)
from pinecall.log.store import Pool
from pinecall.types import KEY_SCOPES, PRODUCTION, Env, an_env

# A revoked key is kept, not deleted: the logs it wrote name it, and a row that vanishes makes
# those unreadable.
_LOOKUP = """
SELECT id, org, label, env, scopes, subject, name
  FROM api_keys
 WHERE hash = $1 AND revoked_at IS NULL
"""

_ISSUE = """
INSERT INTO api_keys (id, hash, org, label, env, scopes, subject, name, created_by)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
"""

_OF_ORG = """
SELECT hash, org, label, env, scopes, subject, name, created_by, created_at, last_used_at,
       revoked_at
  FROM api_keys
 WHERE org = $1
 ORDER BY created_at, id
"""

# Revocation is an UPDATE and never a DELETE, and it is the one that already ran that the WHERE
# filters out: revoking twice must not read as if a live key had just been stopped.
_REVOKE = """
UPDATE api_keys SET revoked_at = now() WHERE hash = $1 AND revoked_at IS NULL
"""

_TOUCH = "UPDATE api_keys SET last_used_at = now() WHERE id = $1"

# What asyncpg answers an UPDATE with when the WHERE matched nothing: the command tag, verbatim.
CHANGED_NOTHING = "UPDATE 0"


class PostgresKeys:
    """Keys in Postgres, found by their hash. The key the app sent never leaves this process."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def verify(self, key: str) -> KeyRecord | None:
        """One indexed lookup on the hash. An unknown or revoked key is None, never an error."""
        row = await self._pool.fetchrow(_LOOKUP, fingerprint(key))
        return None if row is None else _a_record(row)

    async def issue(
        self,
        org: str,
        label: str | None = None,
        *,
        env: Env = PRODUCTION,
        scopes: frozenset[str] = KEY_SCOPES,
        subject: str | None = None,
        name: str | None = None,
        created_by: str | None = None,
    ) -> Issued:
        """The only moment a key exists in the clear: it is minted here, hashed, and let go."""
        key = mint(env, subject)
        record = KeyRecord(
            key_id=a_key_id(),
            org=org,
            label=label,
            env=env,
            scopes=scopes,
            subject=subject,
            name=name,
        )
        await self._pool.execute(
            _ISSUE,
            record.key_id,
            fingerprint(key),
            org,
            label,
            env,
            sorted(scopes),
            subject,
            name,
            created_by,
        )
        return Issued(key=key, record=record)

    async def listed(self, org: str) -> tuple[ListedKey, ...]:
        """Every key of the org, oldest first, revoked ones included and named as revoked."""
        rows = await self._pool.fetch(_OF_ORG, org)
        return tuple(_a_listed_key(row) for row in rows)

    async def revoke(self, hashed: str) -> bool:
        """The command tag says whether a row changed, so revoking a stranger is told apart."""
        tag = await self._pool.execute(_REVOKE, hashed)
        return tag.strip() != CHANGED_NOTHING

    async def touch(self, key_id: str) -> None:
        """One UPDATE by the row's id; a row gone since is nothing to say."""
        await self._pool.execute(_TOUCH, key_id)


def _a_record(row: Any) -> KeyRecord:
    """One row of the lookup as the door reads it: whose key knocked, where, and as whom."""
    return KeyRecord(
        key_id=str(row["id"]),
        org=str(row["org"]),
        label=_text(row["label"]),
        env=an_env(str(row["env"])),
        scopes=frozenset(str(scope) for scope in row["scopes"]),
        subject=_text(row["subject"]),
        name=_text(row["name"]),
    )


def _a_listed_key(row: Any) -> ListedKey:
    """One row of the listing. The hash column IS the fingerprint; there is nothing else to show."""
    revoked = row["revoked_at"]
    used = row["last_used_at"]
    return ListedKey(
        fingerprint=str(row["hash"]),
        org=str(row["org"]),
        label=_text(row["label"]),
        created_at=str(row["created_at"]),
        revoked_at=None if revoked is None else str(revoked),
        env=an_env(str(row["env"])),
        scopes=tuple(sorted(str(scope) for scope in row["scopes"])),
        subject=_text(row["subject"]),
        name=_text(row["name"]),
        created_by=_text(row["created_by"]),
        last_used_at=None if used is None else str(used),
    )


def _text(column: Any) -> str | None:
    """A nullable text column as the record holds it: the string, or None when the row has none."""
    return None if column is None else str(column)
