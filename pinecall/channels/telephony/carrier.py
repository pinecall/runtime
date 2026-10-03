"""What an account is to telephony: who may call the box with it, where it is dialled, its API."""

from dataclasses import dataclass

import httpx

from pinecall.channels.telephony._twilio import Twilio, twilio_of, verify
from pinecall.channels.telephony.carrier_catalog import BOX_CARRIER, known_carrier
from pinecall.domain.errors import Conflict, DeclarationRefused
from pinecall.tenancy.carriers import (
    Account,
    Carrier,
    SipPeer,
    Transport,
    TwilioAccount,
    WhatsappAccount,
)

# Twilio's signalling edges, from the catalog: the fence of every Twilio number on the SFU, and the
# set nftables.conf opens 5060 to. A test holds them equal.
TWILIO_SIGNALLING: tuple[str, ...] = known_carrier(BOX_CARRIER).networks


NOT_PROVISIONED = (
    "the org's account {account} cannot place a call yet: POST /v1/carrier/outbound provisions it"
)


NO_OUTBOUND_HOST = (
    "SIP peer {account} declared no outbound_host: the networks a peer calls FROM are not an "
    "address it takes calls AT"
)


PLACES_NO_CALL = "{account} is a WhatsApp number: it places no call"


ON_NO_TRUNK = "{account} is a WhatsApp number: it is on no SIP trunk"


@dataclass(frozen=True)
class Fence:
    """Who the SFU admits a number's INVITE from: one trunk per fence."""

    trunk: str
    networks: tuple[str, ...]
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class Dialled:
    """Where a call through the account is sent: the host, its transport and the pair."""

    hostname: str
    transport: Transport
    username: str
    password: str


# A trunk that lists no address admits every source: a fence with none approved is no trunk at all.
# `via` is the catalog carrier a number with no account comes through; `own` the approved networks
# of the peer, or of a number hooked with networks of its own (None: it named none).
def fence_of(
    org: str,
    number: str,
    carrier: Carrier | None,
    via: str | None,
    own: tuple[str, ...] | None,
) -> Fence | None:
    """Who the SFU admits the number from, or None until the operator approves a network of it."""
    if carrier is None:
        if own is not None:
            return Fence(trunk=f"{org}:{number}", networks=own) if own else None
        kind = via or BOX_CARRIER
        trunk = org if kind == BOX_CARRIER else f"{org}:{kind}"
        return Fence(trunk=trunk, networks=known_carrier(kind).networks)
    match carrier.account:
        case TwilioAccount():
            return Fence(trunk=org, networks=TWILIO_SIGNALLING)
        case SipPeer():
            peer = carrier.account
            if not own:
                return None
            return Fence(
                trunk=f"{org}:{peer.username}",
                networks=own,
                username=peer.username,
                password=peer.password,
            )
        case WhatsappAccount():
            raise DeclarationRefused(ON_NO_TRUNK.format(account=carrier.id))


def declared_networks(carrier: Carrier) -> tuple[str, ...] | None:
    """The networks a peer says it calls from, for the operator to approve; None for the others."""
    match carrier.account:
        case SipPeer():
            return tuple(carrier.account.addresses)
        case TwilioAccount() | WhatsappAccount():
            return None


def missing_to_dial(carrier: Carrier) -> list[str]:
    """What the account lacks before a call can go out through it, one sentence each."""
    match carrier.account:
        case TwilioAccount():
            return [] if carrier.outbound else [NOT_PROVISIONED.format(account=carrier.id)]
        case SipPeer():
            peer = carrier.account
            return [] if peer.outbound_host else [NO_OUTBOUND_HOST.format(account=carrier.id)]
        case WhatsappAccount():
            return [PLACES_NO_CALL.format(account=carrier.id)]


def dialled_of(carrier: Carrier) -> Dialled:
    """Where a call through the account goes; Conflict naming what is missing."""
    missing = missing_to_dial(carrier)
    if missing:
        raise Conflict(missing[0])
    match carrier.account:
        case TwilioAccount():
            termination = carrier.outbound
            if termination is None:
                raise Conflict(NOT_PROVISIONED.format(account=carrier.id))
            return Dialled(
                hostname=termination.host,
                transport="auto",
                username=termination.username,
                password=termination.password,
            )
        case SipPeer():
            peer = carrier.account
            return Dialled(
                hostname=str(peer.outbound_host),
                transport=peer.outbound_transport,
                username=peer.outbound_username or peer.username,
                password=peer.outbound_password or peer.password,
            )
        case WhatsappAccount():
            raise Conflict(PLACES_NO_CALL.format(account=carrier.id))


# Twilio is the one carrier with an API the box drives; a peer is SIP terms and nothing more.
def control_of(http: httpx.AsyncClient, carrier: Carrier) -> Twilio | None:
    """The account's API the box can list, point and provision numbers through, if it has one."""
    match carrier.account:
        case TwilioAccount():
            return twilio_of(http, carrier.account)
        case SipPeer() | WhatsappAccount():
            return None


def meta_of(carrier: Carrier) -> WhatsappAccount | None:
    """The WhatsApp number at Meta the account is, if it is one."""
    match carrier.account:
        case WhatsappAccount():
            return carrier.account
        case TwilioAccount() | SipPeer():
            return None


async def verify_account(http: httpx.AsyncClient, account: Account) -> None:
    """Refuse an account its carrier does not open with the pair given; a peer is not asked."""
    match account:
        case TwilioAccount():
            await verify(http, account)
        case SipPeer() | WhatsappAccount():
            return
