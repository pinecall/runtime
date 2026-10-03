"""A room offered to a worker: the first offer, another when nobody opened it, then the overflow."""

import asyncio
import logging
import time

from livekit import api

from pinecall.channels import rooms
from pinecall.domain.errors import Conflict, NotAvailable
from pinecall.fleet.roster import Roster, overflow_name
from pinecall.gateway.dispatching import offers
from pinecall.gateway.dispatching._chooser import chosen
from pinecall.gateway.dispatching.offers import Offer
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

# LiveKit waits 10 s on a worker that does not answer before it gives a job up; a live worker opens
# its call in about 2 s. A room no worker opened 12 s after its offer is offered to another.
OFFERED_AGAIN_AFTER_S = 12.0

# Three offers, then the overflow's sentence: 36 s is the longest a caller waits on our account.
OFFERS = 3

SWEPT_EVERY_S = 3.0

# Past any offer: a caller who waited this long has hung up, and the SIP leg is gone.
FORGOTTEN_AFTER_S = 120.0

AT_MOST = 100

OFFERED_AGAIN = "room %s: no worker opened it in %.0f s; offered to %s (offer %d)"

TO_THE_OVERFLOW = "room %s: %s; the overflow says the sentence"


async def offered(pool: Pool, server: api.LiveKitAPI, roster: Roster, offer: Offer) -> str | None:
    """Offer the room to the worker it should go to now; the name it went to, or None if taken."""
    now = time.time()
    target, why = await _target(pool, roster, offer, now)
    if not await offers.offered(pool, offer, target, now):
        return None
    await rooms.dispatched(server, offer.room, target, rooms.read_dispatch(offer.dispatch))
    if why is not None:
        await offers.forgotten(pool, offer.room)
        logger.warning(TO_THE_OVERFLOW, offer.room, why)
    elif offer.offers > 0:
        logger.warning(OFFERED_AGAIN, offer.room, OFFERED_AGAIN_AFTER_S, target, offer.offers + 1)
    return target


async def swept(pool: Pool, server: api.LiveKitAPI, roster: Roster) -> list[str]:
    """Offer again every room nobody opened in time; the rooms offered."""
    now = time.time()
    await offers.aged_out(pool, now - FORGOTTEN_AFTER_S)
    due = await offers.due(pool, now - OFFERED_AGAIN_AFTER_S, AT_MOST)
    return [offer.room for offer in due if await offered(pool, server, roster, offer) is not None]


# A pass that fails is said, and the next one runs: the sweep never stops.
async def sweep_forever(pool: Pool, server: api.LiveKitAPI, roster: Roster) -> None:
    """A pass every few seconds."""
    while True:
        try:
            await swept(pool, server, roster)
        except (Conflict, NotAvailable, api.TwirpError, OSError):
            logger.warning(
                "the offers' sweep failed; the next is in %.0f s", SWEPT_EVERY_S, exc_info=True
            )
        await asyncio.sleep(SWEPT_EVERY_S)


# The overflow when the offers ran out or no worker has a seat; why, so the log says it.
async def _target(pool: Pool, roster: Roster, offer: Offer, now: float) -> tuple[str, str | None]:
    if offer.offers >= OFFERS:
        return overflow_name(offer.fleet), f"no worker opened it in {OFFERS} offers"
    flying = await offers.in_flight(pool, now - OFFERED_AGAIN_AFTER_S)
    tried = () if offer.worker is None else (offer.worker,)
    seat = chosen(roster.of(offer.fleet, now), flying, now, not_these=tried)
    if seat is None or seat.agent_name is None:
        return overflow_name(offer.fleet), "no worker of the fleet has a seat"
    return seat.agent_name, None
