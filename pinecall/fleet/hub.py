"""The fleet loop: the roster and the cloud read, one tick decided, a machine grown or let go."""

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.fleet.roster import accepting_now, heard_lately
from pinecall.wire.rest.fleet import WorkerStatus

type Decision = Grow | Cordon | Delete


# The hostname is the worker's name in its heartbeats: how a seat is matched to a machine.
MACHINE_PREFIX = "pinecall-worker-"


# Generous: a verb may wait for a machine to boot.
A_VERB_MAY_TAKE_S = 120.0


NO_SCRIPT = "no cloud script at {path}: `create <name>`, `delete <name>` and `list` are its verbs"


@dataclass(frozen=True)
class Line:
    """Where the fleet is kept: the busy target, the slack under it, how many, and the graces."""

    # Grow above `target`; shrink only below `target - slack`, so two ticks do not oscillate.
    target: float = 0.6
    slack: float = 0.15
    at_least: int = 1
    at_most: int = 10
    # PINECALL_MAX_JOBS baked into the image: a booting machine counts as this much capacity, so
    # the loop asks once and waits.
    seats_per_worker: int = 4
    # A machine that never heartbeated within the boot grace is deleted; one that fell silent
    # after registering, after this long.
    boot_grace_s: float = 600.0
    gone_after_s: float = 300.0


@dataclass(frozen=True)
class Machine:
    """A machine the cloud lists as the fleet's, and when it was made."""

    name: str
    created_at: float


@dataclass(frozen=True)
class Grow:
    """Make a machine by this name."""

    name: str
    why: str


@dataclass(frozen=True)
class Cordon:
    """Cordon this worker, so it drains and leaves."""

    worker: str
    why: str


@dataclass(frozen=True)
class Delete:
    """Delete a drained, never-booted or long-silent machine."""

    name: str
    why: str


# One executable, three verbs: `create <name>`, `delete <name>`, `list` (name<TAB>ISO 8601 time).
class Cloud:
    """A cloud driven by one script beside the loop, signed in wherever the loop runs."""

    def __init__(self, script: Path) -> None:
        """The script, which must exist."""
        if not script.is_file():
            raise DeclarationRefused(NO_SCRIPT.format(path=script))
        self.script = script

    def machines(self) -> list[Machine]:
        """Every machine the cloud lists as the fleet's."""
        listed = self.ran("list")
        return [_machine(line) for line in listed.splitlines() if line.strip()]

    def ran(self, *verb: str) -> str:
        """One verb run: `create <name>` makes a machine that dials in; `delete <name>` ends it."""
        try:
            done = subprocess.run(
                [str(self.script), *verb],
                capture_output=True,
                text=True,
                check=False,
                timeout=A_VERB_MAY_TAKE_S,
            )
        except subprocess.TimeoutExpired as slow:
            raise UpstreamFailed(
                f"{self.script.name} {' '.join(verb)}: no answer in {A_VERB_MAY_TAKE_S:.0f}s"
            ) from slow
        if done.returncode != 0:
            raise UpstreamFailed(
                f"{self.script.name} {' '.join(verb)}: {done.stderr.strip() or done.returncode}"
            )
        return done.stdout


NUMBERED = re.compile(rf"^{re.escape(MACHINE_PREFIX)}(\d+)$")


# At most one grow or one cordon a tick: a boot takes minutes, and the next tick sees better
# numbers. Every delete that is due goes out. A machine the cloud does not list as the fleet's
# counts in the numbers and is never let go.
def decide(
    seats: Sequence[WorkerStatus], machines: Sequence[Machine], line: Line, now: float
) -> list[Decision]:
    """This tick's decisions from the roster and the cloud."""
    by_name = {seat.worker: seat for seat in seats}
    managed = {machine.name for machine in machines}
    decided: list[Decision] = _drained(seats, managed, now)
    booting = 0
    for machine in machines:
        heard = by_name.get(machine.name)
        if heard is not None and (heard_lately(heard, now) or heard.cordoned):
            continue
        if heard is None and now - machine.created_at <= line.boot_grace_s:
            booting += 1
        elif heard is None:
            decided.append(
                Delete(machine.name, f"never dialled in within {line.boot_grace_s:.0f}s")
            )
        elif now - heard.seen_at > line.gone_after_s:
            decided.append(Delete(machine.name, f"silent for {now - heard.seen_at:.0f}s"))
    counted = [seat for seat in seats if heard_lately(seat, now) and seat.max_jobs is not None]
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
    return decided


def next_name(machines: Sequence[Machine]) -> str:
    """The lowest free `pinecall-worker-N`."""
    taken = {int(found.group(1)) for machine in machines if (found := NUMBERED.match(machine.name))}
    number = 1
    while number in taken:
        number += 1
    return f"{MACHINE_PREFIX}{number}"


def status_line(
    seats: Sequence[WorkerStatus],
    machines: Sequence[Machine],
    line: Line,
    now: float,
    decided: Sequence[Decision],
) -> str:
    """The fleet in one line: what is up, what is held, how busy, and what this tick does."""
    up = [seat for seat in seats if heard_lately(seat, now)]
    active = sum(seat.active for seat in up)
    capacity = sum(seat.max_jobs or 0 for seat in up)
    busy = active / capacity if capacity else 0.0
    accepting = sum(1 for seat in up if accepting_now(seat, now))
    verdict = "hold" if not decided else f"{len(decided)} to do"
    return (
        f"fleet: {len(up)} workers up · {len(machines)} machines · {active}/{capacity} seats held "
        f"· {accepting} accepting · busy {busy:.2f} (target {line.target:.2f}) · {verdict}"
    )


def worded(decision: Decision) -> str:
    """A decision as the terminal prints it."""
    match decision:
        case Grow(name=name, why=why):
            return f"grow    {name}: {why}"
        case Cordon(worker=worker, why=why):
            return f"cordon  {worker}: {why}"
        case Delete(name=name, why=why):
            return f"delete  {name}: {why}"


def applied(decisions: Sequence[Decision], cordon: Callable[[str], None], cloud: Cloud) -> None:
    """Each decision carried out: a machine made, a worker cordoned, a machine deleted."""
    for decision in decisions:
        match decision:
            case Grow(name=name):
                cloud.ran("create", name)
            case Cordon(worker=worker):
                cordon(worker)
            case Delete(name=name):
                cloud.ran("delete", name)


def printed(out: TextIO, text: str) -> None:
    """One line to the terminal, flushed: under systemd stdout is a block-buffered pipe."""
    print(text, file=out, flush=True)


# A silent cordoned worker has drained and left already: deleted without waiting the grace.
def _drained(seats: Sequence[WorkerStatus], managed: set[str], now: float) -> list[Decision]:
    return [
        Delete(
            seat.worker,
            "cordoned and drained" if heard_lately(seat, now) else "cordoned and gone",
        )
        for seat in seats
        if seat.cordoned
        and seat.worker in managed
        and (seat.active == 0 or not heard_lately(seat, now))
    ]


def _room_for_one_more(active: int, capacity: int, workers: int, line: Line) -> str | None:
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


# The quietest worker goes, and only if the fleet stays under `target - slack` without it; ties
# go to the newest, since an older one has warmer caches.
def _one_too_many(
    holding: Sequence[WorkerStatus], managed: set[str], active: int, capacity: int, line: Line
) -> Cordon | None:
    if len(holding) <= line.at_least:
        return None
    candidates = [seat for seat in holding if seat.worker in managed]
    if not candidates:
        return None
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


def _machine(line: str) -> Machine:
    name, _, created = line.partition("\t")
    try:
        when = datetime.fromisoformat(created.strip())
    except ValueError as unreadable:
        raise UpstreamFailed(f"list: {created!r} is not an ISO 8601 time") from unreadable
    return Machine(name=name.strip(), created_at=when.timestamp())
