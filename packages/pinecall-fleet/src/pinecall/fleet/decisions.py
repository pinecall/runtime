"""What the loop does this tick, decided from numbers alone: grow, cordon, delete, or hold."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pinecall.fleet.clouds import Machine
from pinecall.fleet.roster import Seat

# Every machine the loop makes is named so, and numbered from one: the smallest number no
# machine holds. A hostname IS the worker's name in its heartbeats, which is how a seat on the
# hub and a machine at the cloud are the same thing to the loop.
MACHINE_PREFIX = "pinecall-worker-"
NUMBERED = re.compile(rf"^{re.escape(MACHINE_PREFIX)}(\d+)$")


@dataclass(frozen=True)
class Line:
    """The numbers the loop holds the fleet to. Every one of them is a flag of `fleet loop`."""

    # How busy — calls held over seats measured — the fleet is kept. Over it, a machine is asked
    # for; under it by `slack` or more, one is let go. The gap is what keeps two ticks from
    # asking for a machine and cordoning it on the next.
    target: float = 0.6
    slack: float = 0.15
    at_least: int = 1
    at_most: int = 10
    # What a machine still booting will hold once it dials in: the PINECALL_MAX_JOBS baked into
    # the image. Counted as capacity from the moment it is asked for, or the loop would ask again
    # every tick for the minutes a boot takes.
    seats_per_worker: int = 4
    # A machine older than this that never dialled in did not boot: it is deleted, and asked
    # for again if still needed. One that DID dial in and fell silent gets `gone_after_s`.
    boot_grace_s: float = 600.0
    gone_after_s: float = 300.0


@dataclass(frozen=True)
class Grow:
    """Ask the cloud for one more machine, under this name."""

    name: str
    why: str


@dataclass(frozen=True)
class Cordon:
    """Tell this worker to take no new calls and finish the ones it holds."""

    worker: str
    why: str


@dataclass(frozen=True)
class Delete:
    """Delete this machine: it is drained, or it never came, or it has been gone too long."""

    name: str
    why: str


type Decision = Grow | Cordon | Delete


# One pass over the two tables — the roster's seats and the cloud's machines — in the order the
# rules matter: what is finished goes first, then whether there is room, then whether there is
# too much. At most one Grow or one Cordon per tick, because a boot takes minutes and the next
# tick will have better numbers; every Delete that is due goes, because each is already decided.
def decide(
    seats: Sequence[Seat], machines: Sequence[Machine], line: Line, now: float
) -> tuple[Decision, ...]:
    """Everything the loop should do this tick, from what the hub and the cloud say."""
    by_name = {seat.worker: seat for seat in seats}
    managed = {machine.name for machine in machines}
    decided: list[Decision] = []
    decided.extend(_drained(seats, managed, now))
    booting = 0
    for machine in machines:
        heard = by_name.get(machine.name)
        if heard is not None and (heard.heard_lately(now) or heard.cordoned):
            continue
        if heard is None and now - machine.created_at <= line.boot_grace_s:
            booting += 1
        elif heard is None:
            decided.append(
                Delete(machine.name, f"never dialled in within {line.boot_grace_s:.0f}s")
            )
        elif now - heard.seen_at > line.gone_after_s:
            decided.append(Delete(machine.name, f"silent for {now - heard.seen_at:.0f}s"))
    counted = [seat for seat in seats if seat.heard_lately(now) and seat.max_jobs is not None]
    holding = [seat for seat in counted if not seat.cordoned]
    active = sum(seat.active for seat in counted)
    capacity = sum(seat.max_jobs or 0 for seat in holding) + booting * line.seats_per_worker
    workers = len(holding) + booting
    grow = _room_for_one_more(active, capacity, workers, line)
    if grow is not None:
        decided.append(Grow(next_name(machines), grow))
    elif booting == 0:
        letting_go = _one_too_many(holding, managed, active, capacity, line)
        if letting_go is not None:
            decided.append(letting_go)
    return tuple(decided)


def next_name(machines: Sequence[Machine]) -> str:
    """The smallest numbered name no machine holds: pinecall-worker-1, -2, and so on."""
    taken = {int(found.group(1)) for machine in machines if (found := NUMBERED.match(machine.name))}
    number = 1
    while number in taken:
        number += 1
    return f"{MACHINE_PREFIX}{number}"


# A cordoned worker that fell silent LEFT: it drained and exited 3, and a process that is gone
# beats no more. Waiting the five minutes a silent worker gets would keep a paid machine idle for
# nothing — measured on the first real shrink: the worker was gone 5 s after the cordon.
def _drained(seats: Sequence[Seat], managed: set[str], now: float) -> list[Decision]:
    """Every cordoned machine holding no call, or gone quiet since: its work is done and it goes."""
    return [
        Delete(
            seat.worker, "cordoned and drained" if seat.heard_lately(now) else "cordoned and gone"
        )
        for seat in seats
        if seat.cordoned
        and seat.worker in managed
        and (seat.active == 0 or not seat.heard_lately(now))
    ]


def _room_for_one_more(active: int, capacity: int, workers: int, line: Line) -> str | None:
    """Why the fleet needs a machine, or None when it does not."""
    if workers >= line.at_most:
        return None
    if workers < line.at_least:
        return f"{workers} of at least {line.at_least} workers"
    if capacity == 0:
        return "no seat anywhere"
    busy = active / capacity
    if busy > line.target:
        return f"busy {busy:.2f} over {line.target:.2f}"
    return None


# The least loaded managed worker goes, and only when the fleet would still sit UNDER the target
# by the slack without it: a shrink that lands at the target is a grow on the next tick.
def _one_too_many(
    holding: Sequence[Seat], managed: set[str], active: int, capacity: int, line: Line
) -> Cordon | None:
    """The worker to cordon, or None when every one is needed."""
    if len(holding) <= line.at_least:
        return None
    candidates = [seat for seat in holding if seat.worker in managed]
    if not candidates:
        return None
    # Ties go to the newest name: the oldest worker has the warmest caches and the longest record.
    newest_first = sorted(candidates, key=lambda seat: seat.worker, reverse=True)
    quietest = min(newest_first, key=lambda seat: seat.active)
    left = capacity - (quietest.max_jobs or 0)
    if left <= 0:
        return None
    after = active / left
    if after > line.target - line.slack:
        return None
    return Cordon(
        quietest.worker, f"busy {after:.2f} without it, under {line.target - line.slack:.2f}"
    )
