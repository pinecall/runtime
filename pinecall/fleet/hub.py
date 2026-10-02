"""The fleet loop: the roster and the cloud read, one tick decided, a machine grown or let go."""

import math
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import TextIO

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.fleet.roster import accepting_of, heard_lately
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
    # The most machines one tick asks for. 1 is the loop as it was: a fleet far under its target
    # then grows by one machine a tick. The operator raises it with `--grow-at-most`.
    grow_at_most: int = 1


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
# `create` is given the machine's join token and the door to spend it at in its environment
# (PINECALL_JOIN_TOKEN, PINECALL_JOIN_URL), for the machine's first boot.
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

    def ran(self, *verb: str, given: Mapping[str, str] | None = None) -> str:
        """One verb run, what `given` names in its environment; `create` makes, `delete` ends."""
        try:
            done = subprocess.run(
                [str(self.script), *verb],
                capture_output=True,
                text=True,
                check=False,
                timeout=A_VERB_MAY_TAKE_S,
                env=None if given is None else {**os.environ, **given},
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


# A tick grows by what is missing, up to `grow_at_most` machines, or cordons one: a boot takes
# minutes, and the next tick sees better numbers. Every delete that is due goes out. A machine the
# cloud does not list as the fleet's counts in the numbers and is never let go.
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
    wanted, why = _missing(active, capacity, workers, line)
    if wanted:
        decided += [Grow(name, why) for name in free_names(machines, wanted)]
    elif booting == 0:
        letting_go = _one_too_many(holding, managed, active, capacity, line)
        if letting_go is not None:
            decided.append(letting_go)
    return decided


def free_names(machines: Sequence[Machine], count: int) -> list[str]:
    """The `count` lowest free `pinecall-worker-N`."""
    taken = {int(found.group(1)) for machine in machines if (found := NUMBERED.match(machine.name))}
    names: list[str] = []
    number = 1
    while len(names) < count:
        if number not in taken:
            names.append(f"{MACHINE_PREFIX}{number}")
        number += 1
    return names


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
    accepting = len(accepting_of(up, now))
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


def applied(
    decisions: Sequence[Decision],
    cordon: Callable[[str], None],
    cloud: Cloud,
    joining: Callable[[str], Mapping[str, str]],
    forgotten: Callable[[str], None],
) -> None:
    """Each decision done: a machine made with its join, a worker cordoned, one gone, keys too."""
    for decision in decisions:
        match decision:
            case Grow(name=name):
                cloud.ran("create", name, given=joining(name))
            case Cordon(worker=worker):
                cordon(worker)
            case Delete(name=name):
                cloud.ran("delete", name)
                forgotten(name)


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


# The seats missing are those that bring busy back to the target, in whole machines; the
# minimum and a fleet with no seat at all ask for theirs too, and the ceilings cut all of it.
def _missing(active: int, capacity: int, workers: int, line: Line) -> tuple[int, str]:
    reasons: list[tuple[int, str]] = []
    if workers < line.at_least:
        reasons.append((line.at_least - workers, f"{workers} of at least {line.at_least} workers"))
    if capacity == 0:
        reasons.append((1, "no seat anywhere"))
    elif active / capacity > line.target:
        # The flag's decimal as written: 6 / 0.6 in floats is 10.000000000000002, one seat too many.
        seats = math.ceil(Fraction(active) / Fraction(str(line.target))) - capacity
        why = f"busy {active / capacity:.2f} over {line.target:.2f}: {seats} seats missing"
        reasons.append((math.ceil(seats / line.seats_per_worker), why))
    if not reasons:
        return 0, ""
    wanted, why = max(reasons, key=lambda reason: reason[0])
    return max(min(wanted, line.at_most - workers, line.grow_at_most), 0), why


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
