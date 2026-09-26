"""LiveKit's SIP side, the other direction: one outbound trunk per org, the calls it may place."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from livekit import api

from pinecall.routes.inbound_trunks import first_named, once_named
from pinecall.routes.sfu import Sfu
from pinecall.settings import Settings
from pinecall.types import SipTransport

# One outbound trunk per org, named beside its inbound twin so a person reading the SFU's two
# lists sees one pair per tenant. `<fleet>:<org>` is the inbound one, and why a colon (trunks.py).
TRUNK_NAME = "{fleet}:{org}:out"
# Before the fleet led it; renamed in place when found, as the inbound one is (trunks.py).
LEGACY_TRUNK = "pinecall-{org}-out"

NO_LIVEKIT = (
    "this gateway has no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: it cannot place a call on the"
    " media plane"
)

# The word the tenant writes, in the SFU's own enum. `auto` lets livekit-sip choose, which is what
# a Twilio termination and most peers want.
TRANSPORTS: dict[SipTransport, api.SIPTransport] = {
    "auto": api.SIPTransport.SIP_TRANSPORT_AUTO,
    "udp": api.SIPTransport.SIP_TRANSPORT_UDP,
    "tcp": api.SIPTransport.SIP_TRANSPORT_TCP,
    "tls": api.SIPTransport.SIP_TRANSPORT_TLS,
}


@dataclass(frozen=True)
class Placing:
    """What an outbound trunk is made of: where it dials, as whom, and which numbers it may show."""

    address: str
    numbers: tuple[str, ...]
    transport: SipTransport = "auto"
    auth: tuple[str, str] | None = None


class Outbound(Protocol):
    """What the outbound door does on the media plane: keep one trunk per org, and find it again."""

    async def standing(self, org: str) -> str | None:
        """The org's outbound trunk id, or None when the SFU holds none by that name."""
        ...

    async def provisioned(self, org: str, placing: Placing) -> str:
        """The org's outbound trunk, made once and updated after; its id."""
        ...


class LivekitOutbound:
    """The real SFU, over livekit-api: looked up by name before anything is made, never doubled."""

    def __init__(self, sfu: Sfu, fleet: str) -> None:
        self._sfu = sfu
        self._fleet = fleet

    async def standing(self, org: str) -> str | None:
        """One list, matched by the name this runtime gives the org's trunk, or the one it gave."""
        async with self._sfu.api() as livekit:
            trunk = await self._the_orgs_trunk(livekit, org)
            return None if trunk is None else trunk.sip_trunk_id

    async def provisioned(self, org: str, placing: Placing) -> str:
        """Create the trunk when there is none; replace it whole — its name too — when there is."""
        info = _a_trunk_info(TRUNK_NAME.format(fleet=self._fleet, org=org), placing)
        async with self._sfu.api() as livekit:
            trunk = await self._the_orgs_trunk(livekit, org)
            if trunk is None:
                made = await livekit.sip.create_outbound_trunk(
                    api.CreateSIPOutboundTrunkRequest(trunk=info)
                )
                return made.sip_trunk_id
            await livekit.sip.update_outbound_trunk(trunk.sip_trunk_id, info)
            return trunk.sip_trunk_id

    async def _the_orgs_trunk(
        self, livekit: api.LiveKitAPI, org: str
    ) -> api.SIPOutboundTrunkInfo | None:
        """The org's outbound trunk: by the name it carries now, else by the one it once did."""
        standing = await livekit.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
        return first_named(
            standing.items,
            TRUNK_NAME.format(fleet=self._fleet, org=org),
            *once_named(LEGACY_TRUNK, self._fleet, org),
        )


def _a_trunk_info(name: str, placing: Placing) -> api.SIPOutboundTrunkInfo:
    """The trunk as LiveKit keeps it: the org's name, where it dials, and what it dials as."""
    info = api.SIPOutboundTrunkInfo(
        name=name,
        address=placing.address,
        transport=TRANSPORTS[placing.transport],
        numbers=list(placing.numbers),
    )
    if placing.auth is not None:
        info.auth_username, info.auth_password = placing.auth
    return info


def outbound_for(settings: Settings) -> Outbound | None:
    """The SFU when the process has the LiveKit pair; None when it has none: the door says so."""
    sfu = Sfu.of(settings)
    return None if sfu is None else LivekitOutbound(sfu, settings.fleet)
