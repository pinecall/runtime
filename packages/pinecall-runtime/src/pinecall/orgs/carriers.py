"""The carriers table: one per org, its credentials encrypted at rest, read back whole."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pinecall.db import Pool
from pinecall.errors import PinecallError
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, seal, sealed_store, unseal
from pinecall.settings import Settings
from pinecall.types import Carrier, SipPeer, TwilioAccount, parse_carrier_kind, parse_sip_transport

NO_CARRIER = (
    "this org has no carrier yet: PUT /v1/carrier with a Twilio account or a SIP peer first"
)


class NoCarrier(PinecallError):
    """The org brought no carrier: nothing can be imported, provisioned or dialled through yet."""


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


# None when the box was given no vault key: the doors that need one answer 503 in the vault's own
# sentence (NO_VAULT_KEY): a carrier's credentials are a secret exactly as a provider key is.
def carriers_for(settings: Settings, pool: Pool | None) -> Carriers | None:
    """Postgres when the process opened one, memory on a dev key, none when no vault key was set."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.carriers_memory import MemoryCarriers
    from pinecall.orgs.carriers_postgres import PostgresCarriers

    return sealed_store(settings, pool, memory=MemoryCarriers, postgres=PostgresCarriers)


def sealed(cipher: Cipher, carrier: Carrier) -> str:
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
    return seal(cipher, json.dumps(said))


def opened(cipher: Cipher, org: str, kind: str, ciphertext: str) -> Carrier:
    """One row back into the domain's own Carrier, the credentials in the clear."""
    said: dict[str, Any] = json.loads(unseal(cipher, ciphertext))
    if parse_carrier_kind(kind) == "twilio":
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
            outbound_transport=parse_sip_transport(said.get("outbound_transport")),
            outbound_username=said.get("outbound_username"),
            outbound_password=said.get("outbound_password"),
        ),
    )


__all__ = ["NO_VAULT_KEY", "Carriers", "NoVaultKey", "carriers_for"]
