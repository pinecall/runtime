"""The fleet as the hub sees it: every worker's last heartbeat, and the seats those add up to."""

from __future__ import annotations

from dataclasses import dataclass, replace

# livekit-server hands a job only to a worker whose REPORTED load is under its target load, 0.7
# unless livekit.yaml says otherwise (livekit-server pkg/agent/config.go, `DefaultTargetLoad`).
# At or over it the worker is WS_FULL and no dispatch reaches it. The worker's gate reports
# against this line (worker/load.py) and the roster reads it against the same one.
REFUSED_AT = 0.7

# A worker knocks this often with what it holds; one not heard from for STALE_AFTER_S has left,
# crashed, or lost the hub, and is no longer counted as a seat somebody could be routed to.
HEARTBEAT_S = 5.0
STALE_AFTER_S = 30.0

# A seat nobody has heard from in an hour is not a worker that went quiet: its machine was deleted,
# or it was cordoned and left. `fleet list` stops showing it; a stale seat under an hour stays,
# marked gone, so a person sees WHEN it went.
FORGOTTEN_AFTER_S = 3600.0


@dataclass(frozen=True)
class Heartbeat:
    """What one worker says of itself, every HEARTBEAT_S: its name and what it holds."""

    worker: str
    active: int
    # Calls this worker was MEASURED to hold (PINECALL_MAX_JOBS). None: it is gated on the
    # machine's CPU, so it holds no countable seats — the hub's own worker on a full box.
    max_jobs: int | None
    load: float
    draining: bool


@dataclass(frozen=True)
class Standing:
    """What the hub says back: whether this worker was cordoned, and whether the fleet is full."""

    cordoned: bool
    full: bool


@dataclass(frozen=True)
class Seat:
    """One worker as the hub last heard it, and what the hub decided about it since."""

    worker: str
    active: int
    max_jobs: int | None
    load: float
    draining: bool
    cordoned: bool
    seen_at: float

    def heard_lately(self, now: float) -> bool:
        """Whether this worker's last heartbeat is recent enough to count it as up."""
        return now - self.seen_at <= STALE_AFTER_S

    # The four ways a worker takes no new call: it is gone, it was cordoned, it is draining for a
    # restart, or livekit already stopped routing to it at the line.
    def accepting(self, now: float) -> bool:
        """Whether livekit could hand this worker one more call right now."""
        return (
            self.heard_lately(now)
            and not self.cordoned
            and not self.draining
            and self.load < REFUSED_AT
        )

    @property
    def free(self) -> int:
        """Seats this worker still has, by the count it was measured to; 0 when it has no count."""
        if self.max_jobs is None:
            return 0
        return max(0, self.max_jobs - self.active)


@dataclass(frozen=True)
class Totals:
    """The fleet in four numbers: workers up, calls held, seats measured, and who still accepts."""

    workers: int
    active: int
    seats: int
    free: int
    accepting: int

    # Full means somebody is there and nobody can take the call. An empty roster is NOT full: a
    # gateway no worker has knocked at yet is the world before the fleet, and refuses nothing.
    @property
    def full(self) -> bool:
        """Whether a new call has nowhere to go."""
        return self.workers > 0 and self.accepting == 0

    @property
    def busy(self) -> float:
        """Calls held over seats measured, 0.0 when no worker reported a count."""
        return self.active / self.seats if self.seats else 0.0


class Roster:
    """The hub's table of workers: written by their heartbeats, read by the doors and the loop."""

    # One per gateway process, on app.state, never at module level: the roster IS the process's
    # memory of its fleet and a restart forgets it — the next round of heartbeats rebuilds it.
    def __init__(self) -> None:
        self._seats: dict[str, Seat] = {}

    def report(self, beat: Heartbeat, now: float) -> Standing:
        """One heartbeat in, this worker's standing out. A cordon survives every heartbeat."""
        was = self._seats.get(beat.worker)
        seat = Seat(
            worker=beat.worker,
            active=beat.active,
            max_jobs=beat.max_jobs,
            load=beat.load,
            draining=beat.draining,
            cordoned=was.cordoned if was is not None else False,
            seen_at=now,
        )
        self._seats[beat.worker] = seat
        return Standing(cordoned=seat.cordoned, full=self.totals(now).full)

    def cordon(self, worker: str, cordoned: bool = True) -> bool:
        """Mark a worker so its next heartbeat is told to drain; False when nobody has that name."""
        seat = self._seats.get(worker)
        if seat is None:
            return False
        self._seats[worker] = replace(seat, cordoned=cordoned)
        return True

    def seats(self, now: float | None = None) -> tuple[Seat, ...]:
        """Every worker heard from in the last hour, oldest heartbeat first; stale ones say so."""
        if now is not None:
            for name, seat in list(self._seats.items()):
                if now - seat.seen_at > FORGOTTEN_AFTER_S:
                    del self._seats[name]
        return tuple(sorted(self._seats.values(), key=lambda seat: seat.seen_at))

    def totals(self, now: float) -> Totals:
        """The fleet's numbers over the workers heard from lately."""
        up = [seat for seat in self._seats.values() if seat.heard_lately(now)]
        return Totals(
            workers=len(up),
            active=sum(seat.active for seat in up),
            seats=sum(seat.max_jobs or 0 for seat in up),
            free=sum(seat.free for seat in up),
            accepting=sum(1 for seat in up if seat.accepting(now)),
        )
