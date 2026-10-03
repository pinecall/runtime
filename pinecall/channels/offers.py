"""Who takes a call: offered to a worker the gateway chose, then another, then the overflow."""

import logging
import time
from dataclasses import dataclass

from livekit import api

from pinecall.channels import rooms
from pinecall.channels._chooser import chosen
from pinecall.channels.rooms import Dispatch
from pinecall.fleet.roster import Roster, overflow_name
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

# LiveKit waits 10 s on a worker that does not answer before it gives a job up; a live worker opens
# its call in about 2 s. A room no worker opened 12 s after its offer is offered to another.
OFFERED_AGAIN_AFTER_S = 12.0

# Three offers, then the overflow's sentence: 36 s is the longest a caller waits on our account.
OFFERS = 3

# The rooms counted as waiting for a worker: the ones a caller joined in the last ten minutes.
WAITING_COUNTED_S = 600.0

# The first sight stands: a second webhook for the same room changes nothing.
OPENED = """
INSERT INTO offers (room, fleet, dispatch, seen_at)
VALUES (%(room)s, %(fleet)s, %(dispatch)s, %(now)s)
ON CONFLICT (room) DO NOTHING
RETURNING room
"""

# Taken only by the gateway that read this count: two sweeping the same room offer it once.
CLAIMED = """
UPDATE offers SET worker = %(worker)s, offers = offers + 1, offered_at = %(now)s
WHERE room = %(room)s AND offers = %(offers)s
RETURNING room
"""

DUE = """
SELECT room, fleet, dispatch, worker, offers FROM offers
WHERE offered_at IS NULL OR offered_at < %(before)s
ORDER BY seen_at LIMIT %(limit)s
"""

IN_FLIGHT = """
SELECT worker, count(*) AS offered FROM offers
WHERE worker IS NOT NULL AND offered_at >= %(since)s
GROUP BY worker
"""

WAITING = """
SELECT fleet, count(*) AS waiting FROM offers WHERE seen_at >= %(since)s GROUP BY fleet
"""

FORGOTTEN = "DELETE FROM offers WHERE room = %(room)s"

AGED = "DELETE FROM offers WHERE seen_at < %(before)s"

OFFERED_AGAIN = "room %s: no worker opened it in %.0f s; offered to %s (offer %d)"

TO_THE_OVERFLOW = "room %s: %s; the overflow says the sentence"


@dataclass(frozen=True)
class Offer:
    """A room waiting for a worker: its fleet, its call's dispatch, and whom it was offered to."""

    room: str
    fleet: str
    dispatch: str
    worker: str | None
    offers: int


# What a process holds to send a call to a worker: the offers' table, LiveKit, and the fleet's
# workers as their heartbeats say. Every door that sends a call into a room goes through it.
@dataclass(frozen=True)
class Offering:
    """The gateway's dispatcher: a room offered to the worker it chose, by the worker's name."""

    pool: Pool
    server: api.LiveKitAPI
    roster: Roster

    async def offer(self, room: str, fleet: str, dispatch: Dispatch) -> str | None:
        """Send a fleet's worker into the room; the name it went to, or None if it was offered."""
        written = rooms.written(dispatch)
        if not await opened(self.pool, room, fleet, written, time.time()):
            return None
        return await self.offered(
            Offer(room=room, fleet=fleet, dispatch=written, worker=None, offers=0)
        )

    async def offered(self, offer: Offer) -> str | None:
        """Offer the room to the worker it should go to now; the name, or None if taken already."""
        now = time.time()
        target, why = await _target(self.pool, self.roster, offer, now)
        if not await claimed(self.pool, offer, target, now):
            return None
        await rooms.dispatched(self.server, offer.room, target, rooms.read_dispatch(offer.dispatch))
        if why is not None:
            await forgotten(self.pool, offer.room)
            logger.warning(TO_THE_OVERFLOW, offer.room, why)
        elif offer.offers > 0:
            logger.warning(
                OFFERED_AGAIN, offer.room, OFFERED_AGAIN_AFTER_S, target, offer.offers + 1
            )
        return target


async def opened(pool: Pool, room: str, fleet: str, dispatch: str, now: float) -> bool:
    """Keep a room a caller joined; False when it was kept already."""
    values = {"room": room, "fleet": fleet, "dispatch": dispatch, "now": now}
    async with pool.connection() as connection:
        return await (await connection.execute(OPENED, values)).fetchone() is not None


async def claimed(pool: Pool, offer: Offer, worker: str, now: float) -> bool:
    """Say the room is offered to this worker; False when another gateway offered it first."""
    values = {"room": offer.room, "worker": worker, "offers": offer.offers, "now": now}
    async with pool.connection() as connection:
        return await (await connection.execute(CLAIMED, values)).fetchone() is not None


async def due(pool: Pool, before: float, limit: int) -> list[Offer]:
    """The rooms no worker opened and nobody offered since `before`, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(DUE, {"before": before, "limit": limit})).fetchall()
    return [Offer(**row) for row in rows]


async def in_flight(pool: Pool, since: float) -> dict[str, int]:
    """Each worker's rooms offered since `since` that it has not opened."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(IN_FLIGHT, {"since": since})).fetchall()
    return {row["worker"]: row["offered"] for row in rows}


async def waiting(pool: Pool, since: float) -> dict[str, int]:
    """Each fleet's rooms with a caller seen since `since` that no worker opened."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(WAITING, {"since": since})).fetchall()
    return {row["fleet"]: row["waiting"] for row in rows}


async def forgotten(pool: Pool, room: str) -> None:
    """The room is done with: its call opened, the overflow sent, or the room gone."""
    async with pool.connection() as connection:
        await connection.execute(FORGOTTEN, {"room": room})


async def aged_out(pool: Pool, before: float) -> None:
    """Rooms first seen before `before` are past any offer: their callers are gone."""
    async with pool.connection() as connection:
        await connection.execute(AGED, {"before": before})


# The overflow when the offers ran out or no worker has a seat; why, so the log says it.
async def _target(pool: Pool, roster: Roster, offer: Offer, now: float) -> tuple[str, str | None]:
    if offer.offers >= OFFERS:
        return overflow_name(offer.fleet), f"no worker opened it in {OFFERS} offers"
    flying = await in_flight(pool, now - OFFERED_AGAIN_AFTER_S)
    tried = () if offer.worker is None else (offer.worker,)
    seat = chosen(roster.of(offer.fleet, now), flying, now, not_these=tried)
    if seat is None or seat.agent_name is None:
        return overflow_name(offer.fleet), "no worker of the fleet has a seat"
    return seat.agent_name, None
