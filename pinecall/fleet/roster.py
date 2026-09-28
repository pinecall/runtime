"""The roster of workers, one per fleet: who is up, what each holds, and whether a fleet is full."""

from dataclasses import dataclass, replace

from pinecall.wire.rest.fleet import FleetTotals, HeartbeatRequest, HeartbeatResponse

# livekit-server's DefaultTargetLoad: at or over it a worker gets no dispatch.
REFUSED_AT = 0.7
HEARTBEAT_S = 5.0
# A worker silent this long is no longer capacity.
STALE_AFTER_S = 30.0
# A silent worker stays listed, as gone, this long.
FORGOTTEN_AFTER_S = 3600.0


@dataclass(frozen=True)
class WorkerStatus:
    """A worker's last heartbeat, and whether the hub cordoned it."""

    fleet: str
    worker: str
    active: int
    max_jobs: int | None
    load: float
    draining: bool
    cordoned: bool
    seen_at: float

    def heard_lately(self, now: float) -> bool:
        """Whether its last heartbeat is recent enough to count."""
        return now - self.seen_at <= STALE_AFTER_S

    def accepting(self, now: float) -> bool:
        """Whether livekit would dispatch to it: up, not cordoned, not draining, under the line."""
        return (
            self.heard_lately(now)
            and not self.cordoned
            and not self.draining
            and self.load < REFUSED_AT
        )


# In memory: after a restart the next round of heartbeats, five seconds, rebuilds it.
class Roster:
    """The workers of every fleet, by fleet and name, as their heartbeats say."""

    def __init__(self) -> None:
        """Nobody heard from yet."""
        self.seats: dict[tuple[str, str], WorkerStatus] = {}

    def report(self, beat: HeartbeatRequest, now: float) -> HeartbeatResponse:
        """Keep the heartbeat and answer the worker's standing; a cordon outlives heartbeats."""
        was = self.seats.get((beat.fleet, beat.worker))
        cordoned = was is not None and was.cordoned
        self.seats[(beat.fleet, beat.worker)] = WorkerStatus(
            fleet=beat.fleet,
            worker=beat.worker,
            active=beat.active,
            max_jobs=beat.max_jobs,
            load=beat.load,
            draining=beat.draining,
            cordoned=cordoned,
            seen_at=now,
        )
        return HeartbeatResponse(cordoned=cordoned, full=self.totals(beat.fleet, now).full)

    def cordon(self, fleet: str, worker: str, *, on: bool = True) -> bool:
        """Cordon a worker, or take the cordon back; False for a name nobody has."""
        seat = self.seats.get((fleet, worker))
        if seat is None:
            return False
        self.seats[(fleet, worker)] = replace(seat, cordoned=on)
        return True

    def of(self, fleet: str, now: float) -> list[WorkerStatus]:
        """A fleet's workers by last heartbeat, forgetting the ones silent for an hour."""
        for key, seat in list(self.seats.items()):
            if now - seat.seen_at > FORGOTTEN_AFTER_S:
                del self.seats[key]
        return sorted(
            (seat for seat in self.seats.values() if seat.fleet == fleet),
            key=lambda seat: seat.seen_at,
        )

    # A fleet nobody heard from is not full: a gateway with no worker yet refuses nobody.
    def totals(self, fleet: str, now: float) -> FleetTotals:
        """A fleet's workers heard from lately, summed."""
        up = [
            seat for seat in self.seats.values() if seat.fleet == fleet and seat.heard_lately(now)
        ]
        accepting = sum(1 for seat in up if seat.accepting(now))
        return FleetTotals(
            fleet=fleet,
            workers=len(up),
            active=sum(seat.active for seat in up),
            seats=sum(seat.max_jobs or 0 for seat in up),
            free=sum(max(0, (seat.max_jobs or 0) - seat.active) for seat in up if seat.max_jobs),
            accepting=accepting,
            full=bool(up) and accepting == 0,
        )
