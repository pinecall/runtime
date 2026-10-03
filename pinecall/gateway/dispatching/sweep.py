"""The sweep: every few seconds, a room nobody opened in time is offered again, by any gateway."""

import asyncio
import logging
import time

from livekit import api

from pinecall.channels import offers
from pinecall.channels.offers import OFFERED_AGAIN_AFTER_S, Offering
from pinecall.domain.errors import Conflict, NotAvailable

logger = logging.getLogger(__name__)

SWEPT_EVERY_S = 3.0

# Past any offer: a caller who waited this long has hung up, and the SIP leg is gone.
FORGOTTEN_AFTER_S = 120.0

AT_MOST = 100


async def swept(offering: Offering) -> list[str]:
    """Offer again every room nobody opened in time; the rooms offered."""
    now = time.time()
    await offers.aged_out(offering.pool, now - FORGOTTEN_AFTER_S)
    due = await offers.due(offering.pool, now - OFFERED_AGAIN_AFTER_S, AT_MOST)
    return [offer.room for offer in due if await offering.offered(offer) is not None]


# A pass that fails is said, and the next one runs: the sweep never stops.
async def sweep_forever(offering: Offering) -> None:
    """A pass every few seconds."""
    while True:
        try:
            await swept(offering)
        except (Conflict, NotAvailable, api.TwirpError, OSError):
            logger.warning(
                "the offers' sweep failed; the next is in %.0f s", SWEPT_EVERY_S, exc_info=True
            )
        await asyncio.sleep(SWEPT_EVERY_S)
