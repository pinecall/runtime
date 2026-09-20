"""`pinecall-runtime fleet`: list the workers, cordon one, and run the loop that sizes them."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from functools import partial
from typing import Any, TextIO

from pydantic import TypeAdapter

from pinecall.cli.columns import as_columns
from pinecall.cli.operator import Operator, OperatorRefused, against_the_gateway
from pinecall.fleet import STALE_AFTER_S, Line, Seat, cloud_named
from pinecall.fleet.clouds import CloudRefused
from pinecall.fleet.loop import tick

PURPOSE: str = "the workers: list | cordon | uncordon | loop"
VERBS: tuple[str, ...] = ("list", "cordon", "uncordon", "loop")

OPS_FLEET = "/v1/ops/fleet"
SEATS: TypeAdapter[tuple[Seat, ...]] = TypeAdapter(tuple[Seat, ...])

# How often the loop looks. A boot takes minutes and a call takes minutes; fifteen seconds sees
# both without asking the cloud's API four times a minute for nothing.
EVERY_S = 15.0


def configure(parser: argparse.ArgumentParser) -> None:
    """Four verbs, four parsers: each names its arguments, and `fleet` alone prints them."""
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=parser.prog)

    listing = verbs.add_parser("list", help="every worker the hub has heard from, and the totals")
    listing.set_defaults(run=run_list)

    cordoning = verbs.add_parser("cordon", help="a worker takes no new call, drains, and leaves")
    cordoning.add_argument("worker", metavar="<worker>", help="as `fleet list` names it")
    cordoning.set_defaults(run=run_cordon)

    uncordoning = verbs.add_parser("uncordon", help="take a cordon back before the worker left")
    uncordoning.add_argument("worker", metavar="<worker>", help="as `fleet list` names it")
    uncordoning.set_defaults(run=run_uncordon)

    looping = verbs.add_parser("loop", help="keep the fleet at the target: grow, cordon, delete")
    looping.add_argument("--cloud", required=True, help="gcp | aws | hetzner, or a script of yours")
    looping.add_argument(
        "--seats", type=int, required=True, help="PINECALL_MAX_JOBS baked in the image"
    )
    looping.add_argument(
        "--target", type=float, default=Line.target, help=f"busy to hold (default {Line.target})"
    )
    looping.add_argument(
        "--min", type=int, default=Line.at_least, dest="at_least", help="workers, at least"
    )
    looping.add_argument(
        "--max", type=int, default=Line.at_most, dest="at_most", help="workers, at most"
    )
    looping.add_argument(
        "--every",
        type=float,
        default=EVERY_S,
        help=f"seconds between ticks (default {EVERY_S:.0f})",
    )
    looping.add_argument("--once", action="store_true", help="one tick, then exit")
    looping.add_argument(
        "--dry-run", action="store_true", help="say what would be done, do nothing"
    )
    looping.set_defaults(run=run_loop)

    parser.set_defaults(run=partial(_print_the_verbs, parser))


def run_list(_arguments: argparse.Namespace) -> int:
    """Every worker as one line, then the totals."""
    return against_the_gateway(list_workers)


def run_cordon(arguments: argparse.Namespace) -> int:
    """One worker told to drain and leave, on its next heartbeat."""
    return against_the_gateway(partial(cordon, arguments.worker, True))


def run_uncordon(arguments: argparse.Namespace) -> int:
    """One cordon taken back."""
    return against_the_gateway(partial(cordon, arguments.worker, False))


def run_loop(arguments: argparse.Namespace) -> int:
    """The loop, over the cloud named, until interrupted — or one tick with --once."""
    line = Line(
        target=arguments.target,
        at_least=arguments.at_least,
        at_most=arguments.at_most,
        seats_per_worker=arguments.seats,
    )
    try:
        cloud = cloud_named(arguments.cloud)
    except CloudRefused as refused:
        print(refused, file=sys.stderr)
        return 2
    return against_the_gateway(
        partial(loop, cloud, line, arguments.every, arguments.once, arguments.dry_run)
    )


# ── the verbs, as coroutines over an Operator a test can hand in ────────────────


async def list_workers(operator: Operator, out: TextIO = sys.stdout) -> int:
    """The roster and the totals, as a person reads them."""
    said = await operator.get(OPS_FLEET)
    seats = SEATS.validate_python(said["workers"])
    now = float(said["now"])
    if not seats:
        print("no worker has knocked at this gateway yet", file=out)
        return 0
    header = ("worker", "held", "seats", "load", "standing", "heard")
    for line in as_columns([header, *(_row_of(seat, now) for seat in seats)]):
        print(line, file=out)
    totals: dict[str, Any] = said["totals"]
    full = " · FULL" if totals["workers"] and not totals["accepting"] else ""
    print(
        f"\n{totals['workers']} up · {totals['active']} calls · {_seats_of(totals)} · "
        f"{totals['accepting']} accepting{full}",
        file=out,
    )
    return 0


# A worker with no PINECALL_MAX_JOBS is gated by its CPU and reports no count at all, so a fleet
# of those totalled `0 seats free` — which reads exactly like a fleet with nothing left, beside
# the same line saying it accepts calls (box.pinecall.io, 2026-09-20).
def _seats_of(totals: dict[str, Any]) -> str:
    """How much room the fleet has, or that nobody gave it a number to count."""
    if not totals["seats"]:
        return "seats gated by cpu, uncounted"
    return f"{totals['free']} seats free"


async def cordon(worker: str, on: bool, operator: Operator, out: TextIO = sys.stdout) -> int:
    """The cordon set or lifted; a name nobody has is the gateway's own 404."""
    path = f"{OPS_FLEET}/{worker}/cordon"
    if on:
        await operator.post(path)
        print(CORDONED.format(worker=worker), file=out)
        return 0
    await operator.delete(path)
    print(_what_lifting_it_did(worker, await _the_seat_of(operator, worker)), file=out)
    return 0


# A cordon is how a machine is RETIRED: it drains and exits, and the unit is written not to bring
# a drained worker back (`RestartPreventExitStatus=3`, infra/box/pinecall-worker.service), because
# the fleet loop deletes the machine next. Said only as "it leaves", an operator cordons a box to
# look at something, uncordons it, reads `uncordoned`, and has no worker — for thirty seconds
# `fleet list` still says `accepting`, because that is how long a heartbeat counts (2026-09-20).
CORDONED = (
    "{worker} cordoned: it takes no new call, finishes what it holds, and leaves — "
    "and it does not come back on its own"
)
UNCORDONED = "{worker} uncordoned: it takes calls again"
ALREADY_GONE = (
    "{worker} uncordoned, but nothing has been heard from it for {since}s: it already drained and "
    "left, and a drained worker is not restarted — on a box, `systemctl start pinecall-worker`"
)
NOBODY_HEARD_OF = "{worker} uncordoned, and no worker of that name has ever knocked here"


def _what_lifting_it_did(worker: str, seat: Seat | None) -> str:
    """Whether there is still a worker there to take calls again, which is the whole question."""
    if seat is None:
        return NOBODY_HEARD_OF.format(worker=worker)
    since = time.time() - seat.seen_at
    if since > STALE_AFTER_S:
        return ALREADY_GONE.format(worker=worker, since=int(since))
    return UNCORDONED.format(worker=worker)


async def _the_seat_of(operator: Operator, worker: str) -> Seat | None:
    """The roster's row for one worker, as the gateway has it right now."""
    said = await operator.get(OPS_FLEET)
    return next(
        (seat for seat in SEATS.validate_python(said["workers"]) if seat.worker == worker), None
    )


async def loop(
    cloud: Any,
    line: Line,
    every: float,
    once: bool,
    dry_run: bool,
    operator: Operator,
    out: TextIO = sys.stdout,
) -> int:
    """Tick, sleep, tick: the fleet held at the target for as long as this runs."""
    hub = OverTheOperator(operator)
    while True:
        try:
            await tick(hub, cloud, line, time.time(), out, dry_run=dry_run)
        except (CloudRefused, OperatorRefused) as refused:
            print(f"  {refused}", file=out, flush=True)
            if once:
                return 1
        if once:
            return 0
        await asyncio.sleep(every)


class OverTheOperator:
    """The loop's Hub, over the operator API: the roster door and the cordon door."""

    def __init__(self, operator: Operator) -> None:
        self._operator = operator

    async def seats(self) -> tuple[Seat, ...]:
        """Every worker the hub has heard from."""
        return SEATS.validate_python((await self._operator.get(OPS_FLEET))["workers"])

    async def cordon(self, worker: str) -> None:
        """The worker is told on its next heartbeat."""
        await self._operator.post(f"{OPS_FLEET}/{worker}/cordon")


def _row_of(seat: Seat, now: float) -> tuple[str, ...]:
    """One worker as a person reads it."""
    standing = (
        "cordoned"
        if seat.cordoned
        else "draining"
        if seat.draining
        else ("accepting" if seat.accepting(now) else "full" if seat.heard_lately(now) else "gone")
    )
    return (
        seat.worker,
        str(seat.active),
        str(seat.max_jobs) if seat.max_jobs is not None else "cpu",
        f"{seat.load:.2f}",
        standing,
        f"{now - seat.seen_at:.0f}s ago",
    )


def _print_the_verbs(parser: argparse.ArgumentParser, _arguments: Any) -> int:
    """`fleet` with no verb: say what there is, and exit as a help screen does."""
    parser.print_help()
    return 0
