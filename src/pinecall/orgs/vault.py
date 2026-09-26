"""Where a tenant's own provider keys are kept: one row per (org, vendor), encrypted at rest."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from cryptography.fernet import Fernet, MultiFernet

from pinecall._exceptions import PinecallError
from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.types import NO_ORG_KEYS, Brought, ProviderKeys, QuotasOf

# What a runtime with no PINECALL_VAULT_KEY answers when asked to keep somebody's key. A 503 and
# not a 400: the request was right and this box cannot honour it — decisions/provider-keys.md.
NO_VAULT_KEY = "no PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key"

# The library's own message says 32 url-safe base64 bytes without naming the variable an operator
# has to fix. A box must not die on a typo in its environment file without saying which line.
NOT_A_FERNET_KEY = (
    "PINECALL_VAULT_KEY is not a Fernet key, or a comma-separated list of them: generate one with "
    "Fernet.generate_key()"
)

# What every table that seals a tenant secret takes: the box's key, or the keys it has held. A
# Fernet and a MultiFernet seal and open alike, and the tests hand in one key alone.
type Cipher = Fernet | MultiFernet


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

    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher
        self._rows: dict[tuple[str, str], str] = {}

    async def put(self, org: str, vendor: str, key: str) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[(org, vendor)] = seal(self._cipher, key)

    async def drop(self, org: str, vendor: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop((org, vendor), None) is not None

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """In the order a listing reads them, which is alphabetical."""
        return tuple(sorted(vendor for (kept, vendor) in self._rows if kept == org))

    async def keys_of(self, org: str) -> ProviderKeys:
        """Every key this org brought, decrypted."""
        return {
            vendor: unseal(self._cipher, ciphertext)
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

_DROP = "DELETE FROM provider_keys WHERE org = $1 AND vendor = $2 RETURNING vendor"

_VENDORS = "SELECT vendor FROM provider_keys WHERE org = $1 ORDER BY vendor"

_KEYS = "SELECT vendor, ciphertext FROM provider_keys WHERE org = $1"


class PostgresVault:
    """The table in Postgres, read on every call: a key set now is used by the next call."""

    def __init__(self, pool: Pool, cipher: Cipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, org: str, vendor: str, key: str) -> None:
        """The key in the clear reaches this method and nothing under it: the row holds a token."""
        await self._pool.execute(_PUT, org, vendor, seal(self._cipher, key))

    async def drop(self, org: str, vendor: str) -> bool:
        """The row RETURNING says whether one went, so dropping a stranger is told apart."""
        return await self._pool.fetchrow(_DROP, org, vendor) is not None

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """Names only. This is what an operator's listing is built from."""
        return tuple(str(row["vendor"]) for row in await self._pool.fetch(_VENDORS, org))

    async def keys_of(self, org: str) -> ProviderKeys:
        """Every key this org brought, decrypted for the one door that may carry them."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_KEYS, org)
        return {str(row["vendor"]): unseal(self._cipher, str(row["ciphertext"])) for row in rows}


# None is an answer here and not a failure, which is why the vault is the one thing of the process
# that api/deps.py:held does not fetch: a runtime given no vault key holds nobody's key, runs
# every call on the box's own vendor keys, and is a complete self-hosted install.
def vault_for(settings: Settings, pool: Pool | None) -> Vault | None:
    """Postgres when the process opened one, memory on a dev key, none when no key was set."""
    return sealed_store(settings, pool, memory=MemoryVault, postgres=PostgresVault)


# Every table that seals a tenant's secret is built the same way — the carriers, an org's mail,
# its outbound trunk, its identity provider, and this one: none without a vault key, since a
# secret it could not seal is one it must not keep; memory on a laptop with no Postgres up.
def sealed_store[M, P](
    settings: Settings,
    pool: Pool | None,
    *,
    memory: Callable[[Cipher], M],
    postgres: Callable[[Pool, Cipher], P],
) -> M | P | None:
    """Postgres when the process opened one, memory when it did not, none with no vault key."""
    if not settings.vault_key:
        return None
    cipher = build_cipher(settings.vault_key)
    return memory(cipher) if pool is None else postgres(pool, cipher)


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


# Shared with every table that seals a tenant secret (carriers, mail, the trunks, sso, the box's
# own settings): one vault key seals them all. ROTATION: the variable takes a comma-separated
# list, newest first — a secret is sealed under the first key and opened under whichever key it
# was sealed with, so an operator adds the new key at the front, deploys, and drops the old one
# once every row has been written again. One key alone, rotated in place, read every tenant's
# secret as garbage and the box's own settings as "the operator set nothing" (2026-09-26).
def build_cipher(vault_key: str) -> MultiFernet:
    """The box's cipher over every key it has held, or a refusal naming the variable to fix."""
    keys = [key.strip() for key in vault_key.split(",") if key.strip()]
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (ValueError, TypeError) as malformed:
        raise NoVaultKey(NOT_A_FERNET_KEY) from malformed


# The one pair every sealed table spells its secret through: a Fernet token in the row, the
# secret in the clear only in the process that asked for it.
def seal(cipher: Cipher, secret: str) -> str:
    """One secret as a row keeps it: a Fernet token, never the secret itself."""
    return cipher.encrypt(secret.encode()).decode()


def unseal(cipher: Cipher, ciphertext: str) -> str:
    """One row back into the secret its vendor takes."""
    return cipher.decrypt(ciphertext.encode()).decode()
