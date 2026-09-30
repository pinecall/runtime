"""The roster of workers, one per fleet: who is up, what each holds, and whether a fleet is full."""

from collections.abc import Iterable, Sequence
from typing import Literal

from pinecall.wire.rest.fleet import FleetTotals, HeartbeatRequest, HeartbeatResponse, WorkerStatus

type WorkerState = Literal["gone", "cordoned", "draining", "accepting", "failing", "full"]

# livekit-server's DefaultTargetLoad: at or over it a worker gets no dispatch.
REFUSED_AT = 0.7
HEARTBEAT_S = 5.0
# A worker silent this long is no longer capacity.
STALE_AFTER_S = 30.0
# A silent worker stays listed, as gone, this long.
FORGOTTEN_AFTER_S = 3600.0
# A worker up but bad: at least half the calls that ended in its last minute ended in an error,
# judged on four calls at least, or its callers waited over five seconds for first audio at the
# p95, over twenty turns at least. Set so they change nothing a healthy worker does.
FAILED_SHARE = 0.5
ENDED_TO_JUDGE = 4
SLOW_FIRST_AUDIO_S = 5.0
TURNS_TO_JUDGE = 20


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
            ended=beat.ended,
            failed=beat.failed,
            errors=beat.errors,
            turns=beat.turns,
            first_audio_p95_s=beat.first_audio_p95_s,
        )
        return HeartbeatResponse(cordoned=cordoned, full=self.totals(beat.fleet, now).full)

    def cordon(self, fleet: str, worker: str, *, on: bool = True) -> bool:
        """Cordon a worker, or take the cordon back; False for a name nobody has."""
        seat = self.seats.get((fleet, worker))
        if seat is None:
            return False
        self.seats[(fleet, worker)] = seat.model_copy(update={"cordoned": on})
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
            seat for seat in self.seats.values() if seat.fleet == fleet and heard_lately(seat, now)
        ]
        accepting = len(accepting_of(up, now))
        return FleetTotals(
            fleet=fleet,
            workers=len(up),
            active=sum(seat.active for seat in up),
            seats=sum(seat.max_jobs or 0 for seat in up),
            free=sum(max(0, (seat.max_jobs or 0) - seat.active) for seat in up if seat.max_jobs),
            accepting=accepting,
            full=bool(up) and accepting == 0,
        )


def heard_lately(seat: WorkerStatus, now: float) -> bool:
    """Whether the worker's last heartbeat is recent enough to count."""
    return now - seat.seen_at <= STALE_AFTER_S


def accepting_now(seat: WorkerStatus, now: float) -> bool:
    """Whether livekit would dispatch to it: up, not cordoned, not draining, under the line."""
    return (
        heard_lately(seat, now)
        and not seat.cordoned
        and not seat.draining
        and seat.load < REFUSED_AT
    )


def failing(seat: WorkerStatus) -> bool:
    """Whether the worker's last minute is past the line: its calls fail, or its callers wait."""
    ended, failed = seat.ended or 0, seat.failed or 0
    if ended >= ENDED_TO_JUDGE and failed / ended >= FAILED_SHARE:
        return True
    slow = seat.first_audio_p95_s
    return (seat.turns or 0) >= TURNS_TO_JUDGE and slow is not None and slow > SLOW_FIRST_AUDIO_S


# A failing worker is not counted while another of its fleet accepts and is not failing: the line
# never empties a fleet, so a box of one worker per world keeps counting its one.
def accepting_of(seats: Iterable[WorkerStatus], now: float) -> list[WorkerStatus]:
    """The fleet's workers counted as accepting: up and under the line, and not failing."""
    accepting = [seat for seat in seats if accepting_now(seat, now)]
    sound = [seat for seat in accepting if not failing(seat)]
    return sound or accepting


def worker_state(seat: WorkerStatus, fleet: Sequence[WorkerStatus], now: float) -> WorkerState:
    """How the roster counts the worker among its fleet's: gone, cordoned, draining, and so on."""
    if not heard_lately(seat, now):
        return "gone"
    if seat.cordoned:
        return "cordoned"
    if seat.draining:
        return "draining"
    if seat in accepting_of(fleet, now):
        return "accepting"
    return "failing" if accepting_now(seat, now) else "full"
