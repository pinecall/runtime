"""The networks 5060 opens to beyond Twilio's: the carriers admitted and the approved addresses."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Network, ip_network
from pathlib import Path

from pinecall.channels.telephony import carrier_catalog
from pinecall.channels.telephony.carrier_catalog import BOX_CARRIER, known
from pinecall.postgres.pool import Pool
from pinecall.process import box_settings
from pinecall.tenancy import carrier_networks, orgs

logger = logging.getLogger(__name__)


# Read again here, by the root helper that writes nftables: a row the gateway let through still
# opens nothing this wide.
WIDEST_FOR_A_CARRIER = 16


APPLIED = "fence/applied"


# nftables.conf's own set: Twilio's networks are typed there, these are added to it.
SET = "inet pinecall carrier_signalling"


HEADER = "# Written by `pinecall-fence apply`; every edit here is lost on the next minute.\n"


TOO_WIDE = "%s (%s) is not opened: wider than a /%d"


@dataclass(frozen=True)
class Opening:
    """A network 5060 opens to, and why: a carrier's kind, or the org and what it asked for."""

    network: str
    reason: str


@dataclass(frozen=True)
class Applied:
    """When the fence was last written into nftables, and how many networks it added."""

    at: float
    networks: int


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


async def apply(pool: Pool, path: Path, reload: Callable[[], None]) -> Applied:
    """Write the include file when it changed and have nftables read it; stamp what was applied."""
    found = await openings(pool)
    if await asyncio.to_thread(_rewritten, path, _nft_file(found)):
        await asyncio.to_thread(reload)
    applied = Applied(at=time.time(), networks=len(found))
    async with pool.connection() as connection:
        await box_settings.write(
            connection, APPLIED, {"at": applied.at, "networks": applied.networks}
        )
    return applied


async def last_applied(pool: Pool) -> Applied | None:
    """When the root helper last wrote the fence, or None when it never ran on this box."""
    async with pool.connection() as connection:
        value = await box_settings.read(connection, APPLIED)
    at = None if value is None else value.get("at")
    networks = None if value is None else value.get("networks")
    if not isinstance(at, (int, float)) or not isinstance(networks, int):
        return None
    return Applied(at=float(at), networks=networks)


def _rewritten(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    written = path.with_suffix(".new")
    written.write_text(text, encoding="utf-8")
    written.replace(path)
    return True


# The reasons are comment lines of their own: nft reads one element list, nothing inside it.
def _nft_file(found: list[Opening]) -> str:
    if not found:
        return HEADER
    reasons = "".join(f"# {opening.network} {opening.reason}\n" for opening in found)
    elements = ", ".join(opening.network for opening in found)
    return f"{HEADER}{reasons}add element {SET} {{ {elements} }}\n"


def _narrow_enough(opening: Opening) -> bool:
    parsed = ip_network(opening.network, strict=False)
    widest = WIDEST_FOR_A_CARRIER if opening.reason in known() else carrier_networks.WIDEST
    if not isinstance(parsed, IPv4Network) or parsed.prefixlen < widest:
        logger.warning(TOO_WIDE, opening.network, opening.reason, widest)
        return False
    return True
