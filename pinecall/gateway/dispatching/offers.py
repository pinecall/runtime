"""The rooms the gateway offers to workers, in Postgres: a row a room until a worker opens it."""

from dataclasses import dataclass

from pinecall.postgres.pool import Pool

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
OFFERED = """
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


@dataclass(frozen=True)
class Offer:
    """A room waiting for a worker: its fleet, its call's dispatch, and whom it was offered to."""

    room: str
    fleet: str
    dispatch: str
    worker: str | None
    offers: int


async def opened(pool: Pool, room: str, fleet: str, dispatch: str, now: float) -> bool:
    """Keep a room a caller joined; False when it was kept already."""
    values = {"room": room, "fleet": fleet, "dispatch": dispatch, "now": now}
    async with pool.connection() as connection:
        return await (await connection.execute(OPENED, values)).fetchone() is not None


async def offered(pool: Pool, offer: Offer, worker: str, now: float) -> bool:
    """Say the room was offered to this worker; False when another gateway offered it first."""
    values = {"room": offer.room, "worker": worker, "offers": offer.offers, "now": now}
    async with pool.connection() as connection:
        return await (await connection.execute(OFFERED, values)).fetchone() is not None


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
