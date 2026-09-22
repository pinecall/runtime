"""The SFU's SIP side rebuilt from the tables at every start: what the rows say exists, exists."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from pinecall.orgs.carriers import Carriers
from pinecall.orgs.outbound import OutboundTrunks
from pinecall.orgs.table import Orgs
from pinecall.routes.answering import own_numbers
from pinecall.routes.outbound import Outbound, Placing
from pinecall.routes.table import Routes
from pinecall.routes.trunks import Trunks, fence_of
from pinecall.types import Carrier, OutboundTrunk, SipPeer, SipTransport

logger = logging.getLogger(__name__)

# The trunk's transport when it is dialled: a peer's own word, and Twilio's termination takes
# whatever livekit-sip picks. The outbound row does not keep it, because the carrier does.
AUTO: SipTransport = "auto"


# LiveKit keeps every SIP trunk and dispatch rule in Redis, and on 2026-09-22 a box whose Redis
# was recreated without a volume came up with none: three numbers stopped ringing, every dial
# and every warm transfer answered `requested sip trunk does not exist`, and nothing said so —
# the tables still had every row, and the carrier door still read `ready`. The tables ARE the
# provisioning's memory (migration 0031), and every verb they feed looks a trunk up by name before
# making one, so the whole SIP side can be asked for again at any time and nothing is doubled.
# This is that ask: once, when the gateway starts, and per org so one refusal costs one org.
@dataclass(frozen=True)
class Rebuilt:
    """What one pass left on the media plane: which orgs' trunks stand, and which id moved."""

    inbound: tuple[str, ...] = ()
    outbound: tuple[str, ...] = ()
    # Orgs whose outbound trunk came back under a new id — the tell of an SFU that had lost it.
    renumbered: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()


async def reconciled(
    orgs: Orgs,
    carriers: Carriers,
    table: Routes,
    trunks: Trunks,
    outbound_trunks: OutboundTrunks | None,
    outbound: Outbound | None,
) -> Rebuilt:
    """Every org's inbound trunk, numbers and rule, and its outbound trunk, made to stand."""
    inbound: list[str] = []
    placed: list[str] = []
    renumbered: list[str] = []
    refused: list[str] = []
    for org in await orgs.listed():
        carrier = await carriers.of(org.id)
        numbers = await own_numbers(table, org.id)
        if carrier is None or not numbers:
            continue
        try:
            await _inbound(org.id, carrier, numbers, trunks)
            inbound.append(org.id)
            kept = None if outbound_trunks is None else await outbound_trunks.of(org.id)
            if kept is not None and outbound is not None:
                moved = await _outbound(org.id, carrier, numbers, kept, outbound, outbound_trunks)
                placed.append(org.id)
                if moved:
                    renumbered.append(org.id)
        except Exception:  # noqa: BLE001 — one org's SFU refusal must not cost the next org's
            logger.warning("the SFU refused org %s's trunks; the rest go on", org.id, exc_info=True)
            refused.append(org.id)
    return Rebuilt(tuple(inbound), tuple(placed), tuple(renumbered), tuple(refused))


async def _inbound(org: str, carrier: Carrier, numbers: tuple[str, ...], trunks: Trunks) -> None:
    """The org's one inbound trunk with every number on it, exactly as an import admits them."""
    allowed, auth = fence_of(carrier)
    for number in numbers:
        await trunks.admitted(org, number, allowed, auth)


# `provisioned` finds the trunk by name and updates it, or makes it — and a made one has a new
# id. The row is where the dial door reads the id from, so it follows: a row naming a trunk the
# SFU no longer has is exactly the 404 this whole module exists to end.
async def _outbound(
    org: str,
    carrier: Carrier,
    numbers: tuple[str, ...],
    kept: OutboundTrunk,
    outbound: Outbound,
    outbound_trunks: OutboundTrunks | None,
) -> bool:
    """The org's outbound trunk as the row describes it; True when its id on the SFU changed."""
    auth = (kept.username, kept.password) if kept.username and kept.password else None
    placing = Placing(
        address=kept.address, numbers=numbers, transport=_transport_of(carrier), auth=auth
    )
    trunk_id = await outbound.provisioned(org, placing)
    if trunk_id == kept.trunk_id:
        return False
    if outbound_trunks is not None:
        await outbound_trunks.put(replace(kept, trunk_id=trunk_id))
    logger.warning("org %s's outbound trunk was %s and is %s now", org, kept.trunk_id, trunk_id)
    return True


def _transport_of(carrier: Carrier) -> SipTransport:
    """How the org's calls go out: a peer's declared transport, Twilio's whatever livekit picks."""
    return carrier.account.outbound_transport if isinstance(carrier.account, SipPeer) else AUTO
