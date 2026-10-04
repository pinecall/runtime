"""The networks 5060 opens to beyond Twilio's: the carriers admitted and the approved addresses."""

import logging
from dataclasses import dataclass
from ipaddress import IPv4Network, ip_network

from pinecall.channels.telephony import carrier_catalog
from pinecall.channels.telephony.carrier_catalog import BOX_CARRIER, known
from pinecall.postgres.pool import Pool
from pinecall.tenancy import carrier_networks, orgs

logger = logging.getLogger(__name__)


# Checked again at the list's end: a row the gateway let through still opens nothing this wide.
WIDEST_FOR_A_CARRIER = 16


TOO_WIDE = "%s (%s) is not opened: wider than a /%d"


@dataclass(frozen=True)
class Opening:
    """A network 5060 opens to, and why: a carrier's kind, or the org and what it asked for."""

    network: str
    reason: str


async def openings(pool: Pool) -> list[Opening]:
    """Every network beyond Twilio's the fence opens to now, the catalog's first."""
    admitted = await carrier_catalog.admitted(pool)
    found = [
        Opening(network=network, reason=carrier.kind)
        for carrier in known().values()
        if carrier.kind in admitted and carrier.kind != BOX_CARRIER
        for network in carrier.networks
    ]
    slugs = {org.id: org.slug for org in await orgs.listed(pool)}
    found += [
        Opening(network=ask.network, reason=f"{slugs.get(ask.org, ask.org)}: {ask.source}")
        for ask in await carrier_networks.listed(pool, "approved")
    ]
    return [opening for opening in found if _narrow_enough(opening)]


def every_network(found: list[Opening]) -> list[str]:
    """Twilio's networks and every opening: what the cloud's firewall admits to 5060."""
    return list(dict.fromkeys([*known()[BOX_CARRIER].networks, *(o.network for o in found)]))


def _narrow_enough(opening: Opening) -> bool:
    parsed = ip_network(opening.network, strict=False)
    widest = WIDEST_FOR_A_CARRIER if opening.reason in known() else carrier_networks.WIDEST
    if not isinstance(parsed, IPv4Network) or parsed.prefixlen < widest:
        logger.warning(TOO_WIDE, opening.network, opening.reason, widest)
        return False
    return True
