"""The carriers table: one per org, its credentials encrypted at rest, read back whole."""

from __future__ import annotations

import json
from typing import Any, Protocol

from cryptography.fernet import Fernet

from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.orgs.table import DELETED_NOTHING
from pinecall.orgs.vault import NO_VAULT_KEY, NoVaultKey, a_cipher
from pinecall.types import Carrier, SipPeer, TwilioAccount, a_carrier_kind, a_sip_transport


class Carriers(Protocol):
    """Where an org's carrier is kept, replaced whole, and read back with its credentials."""

    async def put(self, carrier: Carrier) -> None:
        """Keep the org's carrier, replacing whatever it had. One per org."""
        ...

    async def of(self, org: str) -> Carrier | None:
        """The org's carrier with its credentials in the clear, or None when it brought none."""
        ...

    async def drop(self, org: str) -> bool:
        """Forget it. False when the org had none: a typo must not read as done."""
        ...


class MemoryCarriers:
    """The table of a clone with a dev key and no Postgres: the same cipher, forgotten on exit."""

    def __init__(self, cipher: Fernet) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[str, str, str]] = {}

    async def put(self, carrier: Carrier) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[carrier.org] = (carrier.kind, carrier.named, _sealed(self._cipher, carrier))

    async def of(self, org: str) -> Carrier | None:
        """Whether there is one, and what it opens to."""
        row = self._rows.get(org)
        return None if row is None else _opened(self._cipher, org, row[0], row[2])

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None


_PUT = """
INSERT INTO carriers (org, kind, account, ciphertext, set_at) VALUES ($1, $2, $3, $4, now())
    ON CONFLICT (org) DO UPDATE
    SET kind = excluded.kind, account = excluded.account, ciphertext = excluded.ciphertext,
        set_at = now()
"""
_OF = "SELECT kind, ciphertext FROM carriers WHERE org = $1"
_DROP = "DELETE FROM carriers WHERE org = $1"


class PostgresCarriers:
    """The table in Postgres, read on every ask: a carrier set now is what the next import uses."""

    def __init__(self, pool: Pool, cipher: Fernet) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, carrier: Carrier) -> None:
        """The credentials reach this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(
            _PUT, carrier.org, carrier.kind, carrier.named, _sealed(self._cipher, carrier)
        )

    async def of(self, org: str) -> Carrier | None:
        """One read on the primary key, decrypted for the door that acts on the account."""
        row = await self._pool.fetchrow(_OF, org)
        if row is None:
            return None
        return _opened(self._cipher, org, str(row["kind"]), str(row["ciphertext"]))

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        tag = await self._pool.execute(_DROP, org)
        return tag.strip() != DELETED_NOTHING


# None when the box was given no vault key: the doors that need one answer 503 in the vault's own
# sentence (NO_VAULT_KEY): a carrier's credentials are a secret exactly as a provider key is.
def carriers_for(settings: Settings, pool: Pool | None) -> Carriers | None:
    """Postgres when the process opened one, memory on a dev key, none when no vault key was set."""
    if not settings.vault_key:
        return None
    cipher = a_cipher(settings.vault_key)
    return MemoryCarriers(cipher) if pool is None else PostgresCarriers(pool, cipher)


def _sealed(cipher: Fernet, carrier: Carrier) -> str:
    """The credentials as a row keeps them: one Fernet token over their JSON."""
    account = carrier.account
    said: dict[str, Any] = (
        {"account_sid": account.account_sid, "user": account.user, "secret": account.secret}
        if isinstance(account, TwilioAccount)
        else {
            "username": account.username,
            "password": account.password,
            "addresses": list(account.addresses),
            "outbound_host": account.outbound_host,
            "outbound_transport": account.outbound_transport,
            "outbound_username": account.outbound_username,
            "outbound_password": account.outbound_password,
        }
    )
    return cipher.encrypt(json.dumps(said).encode()).decode()


def _opened(cipher: Fernet, org: str, kind: str, ciphertext: str) -> Carrier:
    """One row back into the domain's own Carrier, the credentials in the clear."""
    said: dict[str, Any] = json.loads(cipher.decrypt(ciphertext.encode()).decode())
    if a_carrier_kind(kind) == "twilio":
        return Carrier(
            org=org,
            account=TwilioAccount(
                account_sid=str(said["account_sid"]),
                user=str(said["user"]),
                secret=str(said["secret"]),
            ),
        )
    # Read with `.get`: a row written before the outbound half existed has four fewer keys, and
    # it opens as a peer that can be called from and not dialled through, which is what it is.
    return Carrier(
        org=org,
        account=SipPeer(
            username=str(said["username"]),
            password=str(said["password"]),
            addresses=tuple(str(network) for network in said["addresses"]),
            outbound_host=said.get("outbound_host"),
            outbound_transport=a_sip_transport(said.get("outbound_transport")),
            outbound_username=said.get("outbound_username"),
            outbound_password=said.get("outbound_password"),
        ),
    )


__all__ = [
    "NO_VAULT_KEY",
    "Carriers",
    "MemoryCarriers",
    "NoVaultKey",
    "PostgresCarriers",
    "carriers_for",
]
