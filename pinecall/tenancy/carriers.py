"""An org's carrier accounts (Twilio, a SIP peer, WhatsApp at Meta), sealed; the box's Twilio."""

import logging
from dataclasses import dataclass, replace
from ipaddress import ip_network
from typing import Annotated, Literal, Self

from cryptography.fernet import MultiFernet
from psycopg.rows import DictRow
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from pinecall.domain.errors import Conflict, NotAvailable, NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy import vault

type Transport = Literal["auto", "udp", "tcp", "tls"]


logger = logging.getLogger(__name__)


# The box's own Twilio account, sealed in box_settings as credentials/twilio.
BOX_ACCOUNT = "twilio"


NO_CARRIER = "the org has no carrier account: PUT /v1/carrier brings a Twilio account or a SIP peer"


NO_SUCH_ACCOUNT = "the org has no carrier account {account}"


WHICH_ACCOUNT = "the org has {count} carrier accounts ({accounts}): name one of them"


NO_BOX_TWILIO = (
    "this box buys no numbers: it has no Twilio account of its own. Bring a carrier and import one"
)


NOT_A_NETWORK = "{address} is not a network a peer calls from, like 203.0.113.0/24"


HALF_A_PAIR = "outbound_username is given without outbound_password: a peer is dialled with both"


A_SID = r"^(AC|SK)[0-9a-fA-F]{32}$"


AN_ACCOUNT_SID = r"^AC[0-9a-fA-F]{32}$"


CARRIERS = (
    "SELECT kind, account, ciphertext FROM carriers WHERE org = %(org)s ORDER BY set_at, account"
)
CARRIER = (
    "SELECT kind, account, ciphertext FROM carriers WHERE org = %(org)s AND account = %(account)s"
)


PUT_CARRIER = """
INSERT INTO carriers (org, kind, account, label, ciphertext)
VALUES (%(org)s, %(kind)s, %(account)s, %(label)s, %(ciphertext)s)
ON CONFLICT (org, account) DO UPDATE SET kind = excluded.kind, label = excluded.label,
    ciphertext = excluded.ciphertext, set_at = now()
"""


DROP_CARRIER = (
    "DELETE FROM carriers WHERE org = %(org)s AND account = %(account)s RETURNING account"
)


class TwilioAccount(BaseModel):
    """A Twilio account: its SID, and an API key SID with its secret (or the SID and auth token)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["twilio"] = "twilio"
    account_sid: Annotated[str, Field(pattern=AN_ACCOUNT_SID)]
    user: Annotated[str, Field(pattern=A_SID)]
    secret: Annotated[str, Field(min_length=1)]
    label: str = ""


class SipPeer(BaseModel):
    """A PBX or carrier that speaks SIP: where it calls from, its pair, where it is dialled."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["sip"] = "sip"
    username: Annotated[str, Field(min_length=1)]
    password: Annotated[str, Field(min_length=1)]
    addresses: Annotated[list[str], Field(min_length=1)]
    outbound_host: str | None = None
    outbound_transport: Transport = "auto"
    outbound_username: str | None = None
    outbound_password: str | None = None
    label: str = ""

    @field_validator("addresses")
    @classmethod
    def _networks(cls, addresses: list[str]) -> list[str]:
        for address in addresses:
            try:
                ip_network(address, strict=False)
            except ValueError:
                raise ValueError(NOT_A_NETWORK.format(address=address)) from None
        return addresses

    # One account in both directions is what most carriers sell: unsaid, the inbound pair dials.
    @model_validator(mode="after")
    def _a_whole_pair(self) -> Self:
        if self.outbound_username and not self.outbound_password:
            raise ValueError(HALF_A_PAIR)
        return self


class WhatsappAccount(BaseModel):
    """A WhatsApp number at Meta: the id replies are sent from, and the org's token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["whatsapp"] = "whatsapp"
    phone_number_id: Annotated[str, Field(min_length=1)]
    access_token: Annotated[str, Field(min_length=1)]
    label: str = ""


type Account = Annotated[TwilioAccount | SipPeer | WhatsappAccount, Field(discriminator="kind")]


class Termination(BaseModel):
    """How the box dials out through a Twilio account: the host and the credential it made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str
    username: str
    password: str


class _Sealed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account: Account
    outbound: Termination | None = None


@dataclass(frozen=True)
class Carrier:
    """One account of the org at an outside party that holds its numbers."""

    org: str
    account: TwilioAccount | SipPeer | WhatsappAccount
    # Twilio only, written by provisioning: Twilio shows a credential's password once.
    outbound: Termination | None = None

    @property
    def id(self) -> str:
        """The account's own id: Twilio's SID, the peer's username, Meta's phone number id."""
        match self.account:
            case TwilioAccount():
                return self.account.account_sid
            case SipPeer():
                return self.account.username
            case WhatsappAccount():
                return self.account.phone_number_id


ACCOUNT: TypeAdapter[TwilioAccount | SipPeer | WhatsappAccount] = TypeAdapter(Account)


async def carriers_of(pool: Pool, sealed: MultiFernet, org: str) -> list[Carrier]:
    """The org's carrier accounts, oldest first, opened; one sealed under a lost key is skipped."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(CARRIERS, {"org": org})).fetchall()
    opened = (_open_carrier(sealed, org, row) for row in rows)
    return [carrier for carrier in opened if carrier is not None]


async def carrier_named(
    pool: Pool, sealed: MultiFernet, org: str, account: str | None = None
) -> Carrier:
    """The account named, or the org's only one; NotFound or Conflict otherwise."""
    statement = CARRIERS if account is None else CARRIER
    async with pool.connection() as connection:
        rows = await (
            await connection.execute(statement, {"org": org, "account": account})
        ).fetchall()
    carriers = [carrier for carrier in (_open_carrier(sealed, org, row) for row in rows) if carrier]
    if account is not None and not carriers:
        raise NotFound(NO_SUCH_ACCOUNT.format(account=account))
    if not carriers:
        raise NotFound(NO_CARRIER)
    if len(carriers) > 1:
        named = ", ".join(carrier.id for carrier in carriers)
        raise Conflict(WHICH_ACCOUNT.format(count=len(carriers), accounts=named))
    return carriers[0]


async def put_carrier(
    pool: Pool, sealed: MultiFernet, org: str, account: TwilioAccount | SipPeer | WhatsappAccount
) -> Carrier:
    """Keep the account; bringing it again replaces its secret and keeps its termination."""
    kept = Carrier(org=org, account=account)
    was = next(
        (carrier for carrier in await carriers_of(pool, sealed, org) if carrier.id == kept.id),
        None,
    )
    # The termination stays with the account it was made on: only a Twilio account holds one.
    if was is not None and was.account.kind == account.kind:
        kept = replace(kept, outbound=was.outbound)
    await seal_carrier(pool, sealed, kept)
    return kept


async def drop_carrier(
    pool: Pool, sealed: MultiFernet, org: str, account: str | None = None
) -> str:
    """Forget the account; its numbers stay routed until each is let go."""
    gone = await carrier_named(pool, sealed, org, account)
    async with pool.connection() as connection:
        await connection.execute(DROP_CARRIER, {"org": org, "account": gone.id})
    return gone.id


async def box_twilio(pool: Pool, sealed: MultiFernet) -> TwilioAccount:
    """The box's own Twilio account, or NotAvailable when it holds none."""
    carrier = (await vault.box_credentials(pool, sealed)).get(BOX_ACCOUNT)
    try:
        return TwilioAccount.model_validate(carrier)
    except ValidationError:
        raise NotAvailable(NO_BOX_TWILIO) from None


async def seal_carrier(pool: Pool, sealed: MultiFernet, carrier: Carrier) -> None:
    """Write the account sealed, its termination with it."""
    secret = _Sealed(account=carrier.account, outbound=carrier.outbound)
    row = {
        "org": carrier.org,
        "kind": carrier.account.kind,
        "account": carrier.id,
        "label": carrier.account.label,
        "ciphertext": vault.sealed(sealed, secret.model_dump(mode="json")),
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_CARRIER, row)


def _open_carrier(sealed: MultiFernet, org: str, row: DictRow) -> Carrier | None:
    secret = vault.opened(sealed, str(row["ciphertext"]))
    try:
        kept = _Sealed.model_validate(secret)
    except ValidationError:
        logger.warning("org %s's carrier account %s is not one this box reads", org, row["account"])
        return None
    return Carrier(org=org, account=kept.account, outbound=kept.outbound)
