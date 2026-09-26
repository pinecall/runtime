"""Where a tenant's own provider keys are kept: one row per (org, vendor), encrypted at rest."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet

from pinecall.db import Pool
from pinecall.errors import PinecallError
from pinecall.settings import Settings
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


# None is an answer here and not a failure, which is why the vault is the one thing of the process
# that api/deps.py:held does not fetch: a runtime given no vault key holds nobody's key, runs
# every call on the box's own vendor keys, and is a complete self-hosted install.
def vault_for(settings: Settings, pool: Pool | None) -> Vault | None:
    """Postgres when the process opened one, memory on a dev key, none when no key was set."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.vault_memory import MemoryVault
    from pinecall.orgs.vault_postgres import PostgresVault

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
