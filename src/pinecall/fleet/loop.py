"""One tick of the fleet loop: read the hub and the cloud, decide, act, and say what was done."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TextIO

from pinecall.fleet.clouds import Machine
from pinecall.fleet.decisions import Cordon, Decision, Delete, Grow, Line, decide
from pinecall.fleet.roster import Seat, Totals


class Hub(Protocol):
    """What the loop asks the gateway: the roster, and to cordon one worker."""

    async def seats(self) -> tuple[Seat, ...]:
        """Every worker the hub has heard from, stale ones included."""
        ...

    async def cordon(self, worker: str) -> None:
        """The worker takes no new call and drains; its next heartbeat is told."""
        ...


class Cloud(Protocol):
    """What the loop asks the cloud: which machines are the fleet's, one more, one fewer."""

    def machines(self) -> tuple[Machine, ...]:
        """Every machine the fleet's label is on."""
        ...

    def create(self, name: str) -> None:
        """A machine that boots from the image and dials the hub by itself."""
        ...

    def delete(self, name: str) -> None:
        """The machine is gone."""
        ...


# The loop is this function, called every `--every` seconds. It holds nothing between two ticks:
# the roster is the hub's and the machines are the cloud's, so a loop restarted mid-boot sees the
# booting machine in `list` and waits for it like the loop that asked for it would have.
async def tick(
    hub: Hub, cloud: Cloud, line: Line, now: float, out: TextIO, *, dry_run: bool = False
) -> tuple[Decision, ...]:
    """Read both tables, decide, act on every decision (or only say it), and return them."""
    seats = await hub.seats()
    machines = cloud.machines()
    decided = decide(seats, machines, line, now)
    print(_status_line(seats, machines, line, now, decided), file=out)
    for decision in decided:
        print(f"  {_said(decision)}{'  (dry run)' if dry_run else ''}", file=out)
        if not dry_run:
            await _act(decision, hub, cloud)
    return decided


async def _act(decision: Decision, hub: Hub, cloud: Cloud) -> None:
    """One decision onto the table that owns it."""
    match decision:
        case Grow(name=name):
            cloud.create(name)
        case Cordon(worker=worker):
            await hub.cordon(worker)
        case Delete(name=name):
            cloud.delete(name)


def _said(decision: Decision) -> str:
    """One decision as the terminal reads it."""
    match decision:
        case Grow(name=name, why=why):
            return f"grow    {name}: {why}"
        case Cordon(worker=worker, why=why):
            return f"cordon  {worker}: {why}"
        case Delete(name=name, why=why):
            return f"delete  {name}: {why}"


def _status_line(
    seats: Sequence[Seat],
    machines: Sequence[Machine],
    line: Line,
    now: float,
    decided: Sequence[Decision],
) -> str:
    """The fleet in one line: workers, calls over seats, busy against the target, the verdict."""
    up = [seat for seat in seats if seat.heard_lately(now)]
    totals = Totals(
        workers=len(up),
        active=sum(seat.active for seat in up),
        seats=sum(seat.max_jobs or 0 for seat in up),
        free=sum(seat.free for seat in up),
        accepting=sum(1 for seat in up if seat.accepting(now)),
    )
    verdict = "hold" if not decided else f"{len(decided)} to do"
    return (
        f"fleet: {totals.workers} workers up · {len(machines)} machines · "
        f"{totals.active}/{totals.seats} seats held · busy {totals.busy:.2f} "
        f"(target {line.target:.2f}) · {verdict}"
    )
