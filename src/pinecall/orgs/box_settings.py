"""The box_settings table: what the operator configured for the whole box, one row a setting."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.fernet import InvalidToken

from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, a_cipher, opened, sealed

# The rows there are. A name is the whole key: the box is one, so there is no org beside it.
BRAND = "brand"
MAIL = "mail"
# One row per box-wide identity provider, so a second one is a row and not a rewrite.
SIGN_IN = "signin.{provider}"


@dataclass(frozen=True)
class BoxSetting:
    """One setting as it is kept: what may be read back, and its one secret in the clear."""

    value: dict[str, Any]
    # None for a setting that has no secret — the brand — and for one kept without one.
    secret: str | None = None


class BoxSettings(Protocol):
    """Where the operator's own configuration is kept, replaced whole, and read back."""

    async def put(self, name: str, value: dict[str, Any], secret: str | None = None) -> None:
        """Keep this setting, replacing whatever it was. NoVaultKey for a secret and no key."""
        ...

    async def of(self, name: str) -> BoxSetting | None:
        """The setting with its secret in the clear, or None when the operator set none."""
        ...

    async def drop(self, name: str) -> bool:
        """Forget it. False when there was none: a typo must not read as done."""
        ...

    # What came of a letter, written beside the mailbox it went through: a merge, because the
    # standing changes on every send and the mailbox only when the operator says.
    async def noted(self, name: str, changes: dict[str, Any]) -> None:
        """These fields of the value replaced, the rest and the secret kept. Nothing with no row."""
        ...


class MemoryBoxSettings:
    """The table of a clone with no Postgres: the same cipher, forgotten when the process exits."""

    def __init__(self, cipher: Cipher | None) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[dict[str, Any], str | None]] = {}

    async def put(self, name: str, value: dict[str, Any], secret: str | None = None) -> None:
        """Sealed here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[name] = (dict(value), _sealed(self._cipher, secret))

    async def of(self, name: str) -> BoxSetting | None:
        """Through the cipher on the way out, exactly as the row below is."""
        row = self._rows.get(name)
        if row is None:
            return None
        value, ciphertext = row
        return BoxSetting(dict(value), _opened(self._cipher, ciphertext))

    async def drop(self, name: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(name, None) is not None

    async def noted(self, name: str, changes: dict[str, Any]) -> None:
        """The same merge `||` makes below."""
        row = self._rows.get(name)
        if row is not None:
            self._rows[name] = ({**row[0], **changes}, row[1])


# This pool's connections were never taught the jsonb codec (only the log's own are), so jsonb is
# text going out and text coming back: evals/run_store.py says the same.
_PUT = """
INSERT INTO box_settings (name, value, ciphertext, set_at) VALUES ($1, $2::jsonb, $3, now())
    ON CONFLICT (name) DO UPDATE
    SET value = excluded.value, ciphertext = excluded.ciphertext, set_at = now()
"""

logger = logging.getLogger(__name__)

_OF = "SELECT value::text AS value, ciphertext FROM box_settings WHERE name = $1"

_DROP = "DELETE FROM box_settings WHERE name = $1 RETURNING name"

_NOTED = "UPDATE box_settings SET value = value || $2::jsonb WHERE name = $1"


class PostgresBoxSettings:
    """The table in Postgres, read each time it is asked: what is set now is what is used next."""

    def __init__(self, pool: Pool, cipher: Cipher | None) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, name: str, value: dict[str, Any], secret: str | None = None) -> None:
        """The secret reaches this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(_PUT, name, json.dumps(value), _sealed(self._cipher, secret))

    async def of(self, name: str) -> BoxSetting | None:
        """One read on the primary key."""
        row = await self._pool.fetchrow(_OF, name)
        if row is None:
            return None
        ciphertext = row["ciphertext"]
        return BoxSetting(
            json.loads(str(row["value"])),
            _opened(self._cipher, None if ciphertext is None else str(ciphertext)),
        )

    async def drop(self, name: str) -> bool:
        """The command tag says whether a row went, so dropping nothing is told apart."""
        return await self._pool.fetchrow(_DROP, name) is not None

    async def noted(self, name: str, changes: dict[str, Any]) -> None:
        """One UPDATE: a setting nobody made has no row and nothing is written."""
        await self._pool.execute(_NOTED, name, json.dumps(changes))


# Unlike the org tables beside it, this one exists WITHOUT a vault key: the brand is no secret, and
# a box that was given no key may still say what it is called. What it cannot do is keep a secret,
# and it says so where one is handed to it.
def box_settings_for(settings: Settings, pool: Pool | None) -> BoxSettings:
    """Postgres when the process opened one, memory with none; sealing only with a vault key."""
    cipher = a_cipher(settings.vault_key) if settings.vault_key else None
    return MemoryBoxSettings(cipher) if pool is None else PostgresBoxSettings(pool, cipher)


def _sealed(cipher: Cipher | None, secret: str | None) -> str | None:
    """The secret as a row keeps it: a Fernet token, or nothing for a setting that has none."""
    if secret is None:
        return None
    if cipher is None:
        raise NoVaultKey(NO_VAULT_KEY)
    return sealed(cipher, secret)


# A row sealed under a vault key this box no longer holds — lost, or rotated without the old key
# kept behind the new (orgs/vault.py) — reads as a setting with no secret, which every caller
# treats as "not configured": the box falls back to its environment and the operator sets it
# again, rather than every letter dying on InvalidToken. But it is said, once per read, because a
# box whose settings silently went missing is an afternoon of looking in the wrong place.
def _opened(cipher: Cipher | None, ciphertext: str | None) -> str | None:
    """One token back into the secret; None when there is none, or no key to open it with."""
    if ciphertext is None or cipher is None:
        return None
    try:
        return opened(cipher, ciphertext)
    except InvalidToken:
        logger.warning(
            "a box setting is sealed under a key PINECALL_VAULT_KEY no longer holds: read as unset"
        )
        return None
