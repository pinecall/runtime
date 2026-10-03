"""Which worker of a fleet a call is offered to: one heard lately, with a seat free, the freest."""

from collections.abc import Collection, Mapping

from pinecall.fleet.roster import REFUSED_AT, accepting_of
from pinecall.wire.rest.fleet import WorkerStatus

# Two heartbeats and a margin: a worker unheard this long may be gone, and LiveKit would keep a
# dead one for 15 minutes; it is not offered a call until it is heard again.
HEARD_WITHIN_S = 12.0


def chosen(
    seats: Collection[WorkerStatus],
    in_flight: Mapping[str, int],
    now: float,
    not_these: Collection[str] = (),
) -> WorkerStatus | None:
    """The worker to offer a call to, or None when no worker of the fleet has a seat."""
    candidates = [
        seat
        for seat in accepting_of(seats, now)
        if seat.agent_name is not None
        and seat.agent_name not in not_these
        and now - seat.seen_at <= HEARD_WITHIN_S
        and free_seats(seat, in_flight) > 0
    ]
    return max(candidates, key=lambda seat: (free_seats(seat, in_flight), -seat.load), default=None)


# A worker gated on its CPU has no count of seats: one call at a time is offered to it, under
# LiveKit's line.
def free_seats(seat: WorkerStatus, in_flight: Mapping[str, int]) -> int:
    """The seats a worker has free, its calls and the ones offered to it not yet opened taken."""
    offered = in_flight.get(seat.agent_name or "", 0)
    if seat.max_jobs is None:
        return 1 if seat.load < REFUSED_AT and offered == 0 else 0
    return seat.max_jobs - seat.active - offered
