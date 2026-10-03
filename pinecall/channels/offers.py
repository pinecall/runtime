"""Who takes a call: offered to a worker the gateway chose, then another, then the overflow."""

import logging
import time
from dataclasses import dataclass

from livekit import api

from pinecall.channels import rooms
from pinecall.channels._chooser import HEARD_WITHIN_S, any_heard, chosen, free_seats
from pinecall.channels.rooms import Dispatch
from pinecall.fleet.roster import Roster, overflow_name
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

# LiveKit waits 10 s on a worker that does not answer before it gives a job up; a live worker opens
# its call in about 2 s. A room no worker opened 12 s after its offer is offered to another.
OFFERED_AGAIN_AFTER_S = 12.0

# Three offers, then the overflow's sentence: 36 s is the longest a caller waits on our account.
OFFERS = 3

# A fleet none of whose workers was heard is one the gateway knows nothing of yet (it just
# started): the room waits for the next sweep, up to as long as a worker takes to be heard.
WAITS_FOR_A_WORKER_S = HEARD_WITHIN_S

# The rooms counted as waiting for a worker: the ones a caller joined in the last ten minutes.
WAITING_COUNTED_S = 600.0

# Every room is done with by its third offer and a sweep after it: one kept a minute is a room no
# gateway is sweeping.
UNSWEPT_AFTER_S = 60.0

UNSWEPT = "{n} rooms a caller joined over a minute ago were never let go: no gateway sweeps them"

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
SELECT room, fleet, dispatch, seen_at, worker, offers FROM offers
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

KEPT_SINCE = "SELECT count(*) AS kept FROM offers WHERE seen_at < %(before)s"

OFFERED = "room %s: offered to %s: %s"

NOT_YET = "room %s: %s; offered at the next sweep"

OFFERED_AGAIN = "room %s: no worker opened it in %.0f s; offered to %s (offer %d): %s"

TO_THE_OVERFLOW = "room %s: %s; the overflow says the sentence"


@dataclass(frozen=True)
class Offer:
    """A room waiting for a worker: its fleet, its call's dispatch, and whom it was offered to."""

    room: str
    fleet: str
    dispatch: str
    seen_at: float
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
        """Keep the room and offer it; the name it went to, or None if kept already or not yet."""
        written = rooms.written(dispatch)
        now = time.time()
        if not await opened(self.pool, room, fleet, written, now):
            return None
        return await self.offered(
            Offer(room=room, fleet=fleet, dispatch=written, seen_at=now, worker=None, offers=0)
        )

    async def offered(self, offer: Offer) -> str | None:
        """Offer the room to the worker it should go to now; the name, or None if not offered."""
        now = time.time()
        target, why = await _target(self.pool, self.roster, offer, now)
        if target is None:
            logger.info(NOT_YET, offer.room, why)
            return None
        if not await claimed(self.pool, offer, target, now):
            return None
        await rooms.dispatched(self.server, offer.room, target, rooms.read_dispatch(offer.dispatch))
        if target == overflow_name(offer.fleet):
            await forgotten(self.pool, offer.room)
            logger.warning(TO_THE_OVERFLOW, offer.room, why)
        elif offer.offers > 0:
            logger.warning(
                OFFERED_AGAIN, offer.room, OFFERED_AGAIN_AFTER_S, target, offer.offers + 1, why
            )
        else:
            logger.info(OFFERED, offer.room, target, why)
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


async def examined(pool: Pool, now: float) -> str | None:
    """What doctor says of the offers: the rooms kept past every offer, or None."""
    async with pool.connection() as connection:
        row = await (
            await connection.execute(KEPT_SINCE, {"before": now - UNSWEPT_AFTER_S})
        ).fetchone()
    kept = 0 if row is None else row["kept"]
    return UNSWEPT.format(n=kept) if kept else None


# The worker chosen; the overflow when the offers ran out or the workers heard have no seat; None
# while no worker of the fleet was heard yet. And why, so the log says it.
async def _target(pool: Pool, roster: Roster, offer: Offer, now: float) -> tuple[str | None, str]:
    if offer.offers >= OFFERS:
        return overflow_name(offer.fleet), f"no worker opened it in {OFFERS} offers"
    flying = await in_flight(pool, now - OFFERED_AGAIN_AFTER_S)
    tried = () if offer.worker is None else (offer.worker,)
    seats = roster.of(offer.fleet, now)
    seat = chosen(seats, flying, now, not_these=tried)
    if seat is not None and seat.agent_name is not None:
        free = free_seats(seat, flying)
        return seat.agent_name, f"{free} seats free, heard {now - seat.seen_at:.0f} s ago"
    if not any_heard(seats, now) and now - offer.seen_at < WAITS_FOR_A_WORKER_S:
        return None, f"no worker of the fleet heard in {HEARD_WITHIN_S:.0f} s"
    return overflow_name(offer.fleet), "no worker of the fleet has a seat"
