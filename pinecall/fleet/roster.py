"""The roster of workers, one per fleet: who is up, what each holds, and whether a fleet is full."""

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from pinecall.process.shared import Assigned, Shared, newest
from pinecall.process.signal import LocalSignal, Signal
from pinecall.wire.rest.fleet import FleetTotals, HeartbeatRequest, HeartbeatResponse, WorkerStatus

type WorkerState = Literal["gone", "cordoned", "draining", "accepting", "failing", "full"]

# LiveKit's line, the same on both of its sides: livekit-server's agent.DefaultTargetLoad
# (pkg/agent/config.go; a job is offered to a worker whose reported load is under it) and
# livekit-agents' load_threshold (the worker declines at or over it). A worker gated on CPU
# reports its share of the machine, which is that scale. A worker that counts its calls reports
# 0.7 times its calls over its slots to LiveKit (fleet/heartbeat.Load), so LiveKit's full is its
# last slot, and calls over slots to the gateway, full at 1.0: on 2026-10-02 it reported calls over
# slots to both, and LiveKit stopped at 0.7 of them while the gateway still counted seats free.
REFUSED_AT = 0.7
EVERY_SLOT = 1.0
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

# Every gateway says the heartbeats it heard and the cordons it set, once a heartbeat; each answers
# a heartbeat's `full` from totals at most this old, not by walking the fleet every time.
ROSTER_CHANNEL = "roster"
TOTALS_KEPT_S = 1.0
CORDONED = "cordoned"


@dataclass(frozen=True)
class Heard:
    """One gateway's share: the workers that beat on it lately, and the cordons it set."""

    seats: tuple[WorkerStatus, ...] = ()
    cordons: tuple[Assigned, ...] = ()


# In memory, on every gateway: each keeps the heartbeats that reached it and hears the others', so
# a worker beating on any gateway is counted on all of them. After a restart the next round of
# heartbeats, five seconds, rebuilds it; a cordon set on one gateway stands on all, the newest
# setting of a worker's cordon winning.
class Roster:
    """The workers of every fleet, by fleet and name, as their heartbeats say on any gateway."""

    def __init__(self, signal: Signal | None = None) -> None:
        """Nobody heard from yet."""
        # Every gateway's workers, the newest heartbeat of each: what every read answers from.
        self.seats: dict[tuple[str, str], WorkerStatus] = {}
        self.heard_here: dict[tuple[str, str], WorkerStatus] = {}
        self.cordons: dict[tuple[str, str], Assigned] = {}
        self.cordoned: dict[tuple[str, str], str] = {}
        self.shared = Shared(signal or LocalSignal(), ROSTER_CHANNEL, _HEARD, Heard(), self._merged)
        self.shared.every_s = HEARTBEAT_S
        self.shared.gathered = self._gathered
        self._totals: dict[str, tuple[float, FleetTotals]] = {}

    async def start(self) -> None:
        """Hear the other gateways' workers, and say this one's every heartbeat."""
        await self.shared.start()

    async def close(self) -> None:
        """Stop hearing and saying."""
        await self.shared.close()

    def report(self, beat: HeartbeatRequest, now: float) -> HeartbeatResponse:
        """Keep the heartbeat and answer the worker's standing; a cordon outlives heartbeats."""
        cordoned = (beat.fleet, beat.worker) in self.cordoned
        seat = WorkerStatus(
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
        self.heard_here[(beat.fleet, beat.worker)] = seat
        self.seats[(beat.fleet, beat.worker)] = seat
        return HeartbeatResponse(cordoned=cordoned, full=self._kept_totals(beat.fleet, now).full)

    def cordon(self, fleet: str, worker: str, *, on: bool = True) -> bool:
        """Cordon a worker, or take the cordon back; False for a name nobody has."""
        if (fleet, worker) not in self.seats:
            return False
        holder = CORDONED if on else None
        self.cordons[(fleet, worker)] = Assigned(fleet, worker, holder, time.time())
        self._merged()
        self.shared.put(self._gathered())
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

    def _kept_totals(self, fleet: str, now: float) -> FleetTotals:
        kept = self._totals.get(fleet)
        if kept is not None and 0 <= now - kept[0] < TOTALS_KEPT_S:
            return kept[1]
        totals = self.totals(fleet, now)
        self._totals[fleet] = (now, totals)
        return totals

    def _gathered(self) -> Heard:
        return Heard(seats=tuple(self.heard_here.values()), cordons=tuple(self.cordons.values()))

    # Each worker's newest heartbeat, wherever it beat; each cordon's newest setting.
    def _merged(self) -> None:
        theirs = [heard.share for heard in self.shared.theirs.values()]
        settings = (setting for share in theirs for setting in share.cordons)
        self.cordoned = newest(self.cordons, settings)
        merged = dict(self.heard_here)
        for share in theirs:
            for seat in share.seats:
                known = merged.get((seat.fleet, seat.worker))
                if known is None or known.seen_at < seat.seen_at:
                    merged[(seat.fleet, seat.worker)] = seat
        self.seats = {
            key: seat.model_copy(update={"cordoned": key in self.cordoned})
            for key, seat in merged.items()
        }

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


_HEARD: TypeAdapter[Heard] = TypeAdapter(Heard)


def heard_lately(seat: WorkerStatus, now: float) -> bool:
    """Whether the worker's last heartbeat is recent enough to count."""
    return now - seat.seen_at <= STALE_AFTER_S


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
    accepting = [seat for seat in seats if _accepting_now(seat, now)]
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
    return "failing" if _accepting_now(seat, now) else "full"


def refused_at(max_jobs: int | None) -> float:
    """The load a heartbeat says full at: every slot of a worker that counts, 0.7 of a CPU."""
    return REFUSED_AT if max_jobs is None else EVERY_SLOT


def _accepting_now(seat: WorkerStatus, now: float) -> bool:
    """Whether livekit would dispatch to it: up, not cordoned, not draining, under its line."""
    return (
        heard_lately(seat, now)
        and not seat.cordoned
        and not seat.draining
        and seat.load < refused_at(seat.max_jobs)
    )
