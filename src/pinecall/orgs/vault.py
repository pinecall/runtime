"""Where a tenant's own provider keys are kept: one row per (org, vendor), encrypted at rest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from cryptography.fernet import Fernet

from pinecall._exceptions import PinecallError
from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.orgs.table import DELETED_NOTHING
from pinecall.types import NO_ORG_KEYS, Brought, ProviderKeys, QuotasOf

# What a runtime with no PINECALL_VAULT_KEY answers when asked to keep somebody's key. A 503 and
# not a 400: the request was right and this box cannot honour it — decisions/provider-keys.md.
NO_VAULT_KEY = "no PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key"

# The library's own message says 32 url-safe base64 bytes without naming the variable an operator
# has to fix. A box must not die on a typo in its environment file without saying which line.
NOT_A_FERNET_KEY = "PINECALL_VAULT_KEY is not a Fernet key: generate one with Fernet.generate_key()"


class NoVaultKey(PinecallError):
    """PINECALL_VAULT_KEY is set to something Fernet cannot read; the process says so and stops."""


class Vault(Protocol):
    """Where an org's own provider keys are put, dropped, listed by name and read back whole."""

    async def put(self, org: str, vendor: str, key: str) -> None:
        """Keep the org's own key for one vendor, replacing whatever it had for that vendor."""
        ...

    async def drop(self, org: str, vendor: str) -> bool:
        """Forget it. False when the org had no key for that vendor: a typo must not read done."""
        ...

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """Which vendors the org brought a key for, by name. Never a value and never a prefix."""
        ...

    async def keys_of(self, org: str) -> ProviderKeys:
        """The org's keys in the clear, for the one door a worker reads them through."""
        ...


class MemoryVault:
    """The vault of a clone with a dev key and no Postgres: the same cipher, forgotten on exit."""

    def __init__(self, cipher: Fernet) -> None:
        self._cipher = cipher
        self._rows: dict[tuple[str, str], str] = {}

    async def put(self, org: str, vendor: str, key: str) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[(org, vendor)] = _sealed(self._cipher, key)

    async def drop(self, org: str, vendor: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop((org, vendor), None) is not None

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """In the order a listing reads them, which is alphabetical."""
        return tuple(sorted(vendor for (kept, vendor) in self._rows if kept == org))

    async def keys_of(self, org: str) -> ProviderKeys:
        """Every key this org brought, decrypted."""
        return {
            vendor: _opened(self._cipher, ciphertext)
            for (kept, vendor), ciphertext in self._rows.items()
            if kept == org
        }


# One row per (org, vendor), replaced whole: a tenant who rotates a key sets it again and the
# previous ciphertext goes with it. There is deliberately no history of a secret in this table.
_PUT = """
INSERT INTO provider_keys (org, vendor, ciphertext, set_at)
    VALUES ($1, $2, $3, now())
    ON CONFLICT (org, vendor) DO UPDATE
    SET ciphertext = excluded.ciphertext, set_at = now()
"""

_DROP = "DELETE FROM provider_keys WHERE org = $1 AND vendor = $2"

_VENDORS = "SELECT vendor FROM provider_keys WHERE org = $1 ORDER BY vendor"

_KEYS = "SELECT vendor, ciphertext FROM provider_keys WHERE org = $1"


class PostgresVault:
    """The table in Postgres, read on every call: a key set now is used by the next call."""

    def __init__(self, pool: Pool, cipher: Fernet) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, org: str, vendor: str, key: str) -> None:
        """The key in the clear reaches this method and nothing under it: the row holds a token."""
        await self._pool.execute(_PUT, org, vendor, _sealed(self._cipher, key))

    async def drop(self, org: str, vendor: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        tag = await self._pool.execute(_DROP, org, vendor)
        return tag.strip() != DELETED_NOTHING

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """Names only. This is what an operator's listing is built from."""
        return tuple(str(row["vendor"]) for row in await self._pool.fetch(_VENDORS, org))

    async def keys_of(self, org: str) -> ProviderKeys:
        """Every key this org brought, decrypted for the one door that may carry them."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_KEYS, org)
        return {str(row["vendor"]): _opened(self._cipher, str(row["ciphertext"])) for row in rows}


# None is an answer here and not a failure, which is why the vault is the one thing of the process
# that api/_deps.py:held does not fetch: a runtime given no vault key holds nobody's key, runs
# every call on the box's own vendor keys, and is a complete self-hosted install.
def vault_for(settings: Settings, pool: Pool | None) -> Vault | None:
    """Postgres when the process opened one, memory on a dev key, none when no key was set."""
    if not settings.vault_key:
        return None
    cipher = a_cipher(settings.vault_key)
    return MemoryVault(cipher) if pool is None else PostgresVault(pool, cipher)


# Every door that opens a call — the worker's own, the chat socket, an eval run — asks this one
# question and never the vault directly: whose keys is this call running on. A runtime with no
# vault answers the empty set, which is the truth there and never a refusal.
async def keys_brought_by(vault: Vault | None, org: str) -> ProviderKeys:
    """The org's own keys, read now: a key rotated a moment ago is the one the next call runs."""
    return NO_ORG_KEYS if vault is None else await vault.keys_of(org)


# What a call's vendors are built from: the org's own keys and what the box lends it, read
# together and now — a quota set a moment ago bites the next call, like the key rotated. Every
# door that builds a model, an ear or a voice for an org asks this; only memory's embedder, which
# runs on the box's key whatever the org is lent, asks keys_brought_by alone.
# `quotas_of` is `Orgs.quotas_of` where a door holds the table and `Admission.quotas_of` where it
# holds the gate, which reads the same row: one question, whoever is asked it.
async def brought_by(vault: Vault | None, quotas_of: QuotasOf, org: str) -> Brought:
    """The org's keys and what the box lends it, for the call about to be built."""
    return Brought(keys=await keys_brought_by(vault, org), lends=(await quotas_of(org)).lends)


# Shared with the carriers table (orgs/carriers.py): one vault key seals every tenant secret.
def a_cipher(vault_key: str) -> Fernet:
    """The box's Fernet, or a refusal that names the variable an operator has to fix."""
    try:
        return Fernet(vault_key.encode())
    except (ValueError, TypeError) as malformed:
        raise NoVaultKey(NOT_A_FERNET_KEY) from malformed


def _sealed(cipher: Fernet, key: str) -> str:
    """One provider key as a row keeps it: a Fernet token, never the key itself."""
    return cipher.encrypt(key.encode()).decode()


def _opened(cipher: Fernet, ciphertext: str) -> str:
    """One row back into the key a vendor takes."""
    return cipher.decrypt(ciphertext.encode()).decode()
