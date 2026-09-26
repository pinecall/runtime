"""The box_settings table: what the operator configured for the whole box, one row a setting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.fernet import InvalidToken

from pinecall.db import Pool
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, build_cipher, seal, unseal
from pinecall.settings import Settings

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


logger = logging.getLogger(__name__)


# Unlike the org tables beside it, this one exists WITHOUT a vault key: the brand is no secret, and
# a box that was given no key may still say what it is called. What it cannot do is keep a secret,
# and it says so where one is handed to it.
def box_settings_for(settings: Settings, pool: Pool | None) -> BoxSettings:
    """Postgres when the process opened one, memory with none; sealing only with a vault key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.box_settings_memory import MemoryBoxSettings
    from pinecall.orgs.box_settings_postgres import PostgresBoxSettings

    cipher = build_cipher(settings.vault_key) if settings.vault_key else None
    return MemoryBoxSettings(cipher) if pool is None else PostgresBoxSettings(pool, cipher)


def sealed(cipher: Cipher | None, secret: str | None) -> str | None:
    """The secret as a row keeps it: a Fernet token, or nothing for a setting that has none."""
    if secret is None:
        return None
    if cipher is None:
        raise NoVaultKey(NO_VAULT_KEY)
    return seal(cipher, secret)


# A row sealed under a vault key this box no longer holds — lost, or rotated without the old key
# kept behind the new (orgs/vault.py) — reads as a setting with no secret, which every caller
# treats as "not configured": the box falls back to its environment and the operator sets it
# again, rather than every letter dying on InvalidToken. But it is said, once per read, because a
# box whose settings silently went missing is an afternoon of looking in the wrong place.
def opened(cipher: Cipher | None, ciphertext: str | None) -> str | None:
    """One token back into the secret; None when there is none, or no key to open it with."""
    if ciphertext is None or cipher is None:
        return None
    try:
        return unseal(cipher, ciphertext)
    except InvalidToken:
        logger.warning(
            "a box setting is sealed under a key PINECALL_VAULT_KEY no longer holds: read as unset"
        )
        return None
