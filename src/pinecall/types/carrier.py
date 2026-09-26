"""Carrier: whose numbers reach this org's agents, how calls get to the box, and how they leave."""

import ipaddress
from dataclasses import dataclass
from typing import Literal, cast, get_args

from pinecall.types.dialling import SIP_TRANSPORTS, SipTransport
from pinecall.types.refused import DeclarationRefused

# Two ways a number reaches the box. Twilio: the tenant's own account, whose trunk the gateway
# points at the box and attaches numbers to over Twilio's API. sip: any other carrier, or a PBX,
# that the tenant points at the box themselves — the gateway only has to let it in, by the
# credentials and the addresses the tenant declares. What they share is the other end: one
# LiveKit inbound trunk per org, with the org's numbers on it.
type CarrierKind = Literal["twilio", "sip"]
CARRIER_KINDS: frozenset[str] = frozenset(get_args(CarrierKind.__value__))

# Twilio's account SIDs and API key SIDs are 34 characters with a two-letter prefix.
_A_TWILIO_SID = ("AC", "SK")
TWILIO_SID_LENGTH = 34


@dataclass(frozen=True)
class TwilioAccount:
    """A tenant's Twilio: the account, and what authenticates the gateway to it."""

    account_sid: str
    # An API key SID and its secret, or the account SID again and the auth token.
    user: str
    secret: str

    def __post_init__(self) -> None:
        for sid in (self.account_sid, self.user):
            if len(sid) != TWILIO_SID_LENGTH or not sid.startswith(_A_TWILIO_SID):
                raise DeclarationRefused(
                    f"a Twilio SID is 34 characters starting with AC or SK, not {sid[:6]!r}…"
                )
        if not self.secret:
            raise DeclarationRefused("a Twilio account needs its auth token or an API key's secret")


@dataclass(frozen=True)
class SipPeer:
    """A carrier or a PBX the tenant points at the box: who it says it is, and where from."""

    username: str
    password: str
    # The networks its INVITEs come from, CIDR: the fence the box opens for it, and nothing else.
    addresses: tuple[str, ...]
    # Outbound, and every one of them optional: the four fields answer the other direction, where
    # the box places the INVITE instead of receiving one. A peer that declared no `outbound_host`
    # can be called FROM and never dialled THROUGH — the doors say exactly that, by name, rather
    # than guessing that an address it sends from is one it would accept a call at.
    outbound_host: str | None = None
    outbound_transport: SipTransport = "auto"
    # What the peer asks the box to authenticate with when the box calls it. Unsaid, the same
    # credentials it registers with: one account in both directions is what most carriers sell,
    # and a peer that separates them says so here.
    outbound_username: str | None = None
    outbound_password: str | None = None

    def __post_init__(self) -> None:
        if not self.username or not self.password:
            raise DeclarationRefused("a SIP peer registers with a username and a password")
        if not self.addresses:
            raise DeclarationRefused("a SIP peer names at least one network its calls come from")
        for network in self.addresses:
            try:
                ipaddress.ip_network(network, strict=False)
            except ValueError:
                raise DeclarationRefused(f"{network!r} is not a network in CIDR form") from None
        if self.outbound_transport not in SIP_TRANSPORTS:
            raise DeclarationRefused(
                f"a SIP peer is dialled over one of {sorted(SIP_TRANSPORTS)}, "
                f"not {self.outbound_transport!r}"
            )
        if self.outbound_username and not self.outbound_password:
            raise DeclarationRefused("a SIP peer dialled with a username needs its password")

    @property
    def dialled_at(self) -> str | None:
        """Where an INVITE this box places goes, or None when the peer declared nowhere."""
        return self.outbound_host or None

    @property
    def dialled_as(self) -> tuple[str, str]:
        """What the box authenticates as when it calls the peer: its own pair, or the one above."""
        if self.outbound_username and self.outbound_password:
            return (self.outbound_username, self.outbound_password)
        return (self.username, self.password)


@dataclass(frozen=True)
class Carrier:
    """One org's carrier: which kind, and the credentials the gateway acts with."""

    org: str
    account: TwilioAccount | SipPeer

    @property
    def kind(self) -> CarrierKind:
        """Which of the two it is, read off the credentials it holds."""
        return "twilio" if isinstance(self.account, TwilioAccount) else "sip"

    @property
    def named(self) -> str:
        """The one thing a listing may say about the credentials: the account SID, or the user."""
        return (
            self.account.account_sid
            if isinstance(self.account, TwilioAccount)
            else self.account.username
        )


def a_carrier_kind(word: str) -> CarrierKind:
    """The kind this word names, or a refusal that lists the two."""
    if word not in CARRIER_KINDS:
        raise DeclarationRefused(f"a carrier is one of {sorted(CARRIER_KINDS)}, not {word!r}")
    return cast("CarrierKind", word)


# The other direction, provisioned once and remembered: a LiveKit SIP outbound trunk, pointed at
# the org's own Twilio termination or at its peer, with the credentials the far side asks for. It
# is remembered rather than read back because neither vendor shows a trunk's password twice.
@dataclass(frozen=True)
class OutboundTrunk:
    """The trunk one org places its calls through: the SFU's id for it, and what it dials as."""

    org: str
    kind: CarrierKind
    trunk_id: str
    address: str
    username: str | None = None
    password: str | None = None

    def __post_init__(self) -> None:
        if not self.trunk_id or not self.address:
            raise DeclarationRefused("an outbound trunk has an id on the SFU and somewhere to dial")

    @property
    def auth(self) -> tuple[str, str] | None:
        """The pair the SFU authenticates with, or None for a trunk that needs none."""
        if self.username is None or self.password is None:
            return None
        return (self.username, self.password)
