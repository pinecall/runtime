"""`traceback <number>`: every phone call with a number and every dial to it, for a carrier."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from pinecall.domain.errors import DeclarationRefused
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from pinecall.tenancy import reads, traceback
from pinecall.wire.rest.ops import Traceback, TracebackCall, TracebackDial

NOT_A_DAY = "--since {since}: a day, like 2026-09-01"

NOTHING = "no call with {number} and no dial to it since {since}"


def traceback_verb(verb: argparse.ArgumentParser) -> None:
    """`traceback <number> [--since YYYY-MM-DD]`."""
    verb.add_argument("number")
    verb.add_argument("--since", default=None, help="a day, YYYY-MM-DD; 24 months back unset")
    verb.set_defaults(run=traced)


def traced(settings: Settings, args: argparse.Namespace) -> int:
    """Print the number's calls, kept or erased, and its dials, oldest first."""
    since = _since(args.since)
    found = asyncio.run(_found(settings, args.number, since))
    for line in lines_of(found):
        sys.stdout.write(f"{line}\n")
    return 0


def lines_of(found: Traceback) -> list[str]:
    """The traceback as lines a person pastes into an answer to a carrier."""
    if not found.calls and not found.dials:
        return [NOTHING.format(number=found.number, since=_day(found.since))]
    calls = [_call_line(call) for call in found.calls]
    dials = [_dial_line(dial) for dial in found.dials]
    return [
        f"{found.number} since {_day(found.since)}",
        f"calls ({len(calls)})",
        *calls,
        f"dials ({len(dials)})",
        *dials,
    ]


async def _found(settings: Settings, number: str, since: float | None) -> Traceback:
    pool = await open_pool(settings.database_url)
    try:
        found = await traceback.of_number(pool, number, since)
        await traceback.read_by(pool, found, reads.OPERATOR)
        return found
    finally:
        await pool.close()


def _since(day: str | None) -> float | None:
    if day is None:
        return None
    try:
        return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    except ValueError:
        raise DeclarationRefused(NOT_A_DAY.format(since=day)) from None


def _call_line(call: TracebackCall) -> str:
    lasted = "-"
    if call.started_at is not None and call.ended_at is not None:
        lasted = f"{round(call.ended_at - call.started_at)}s"
    kept = "erased, record kept" if call.erased else "kept"
    return (
        f"  {_when(call.started_at)}  {call.direction or '-':8}  {call.from_number or '-'} -> "
        f"{call.to_number or '-'}  {lasted:>6}  {call.end_reason or '-'}  "
        f"org {call.org or '-'} {call.env or '-'}  {call.call}  {kept}"
    )


def _dial_line(dial: TracebackDial) -> str:
    outcome = f"refused: {dial.refused}" if dial.refused else f"placed {dial.call or '-'}"
    return (
        f"  {_when(dial.at)}  shown {dial.shown or '-'}  org {dial.org} {dial.env}  "
        f"agent {dial.agent}  asked by {dial.asked_by}  {outcome}"
    )


def _when(at: float | None) -> str:
    return "-" * 16 if at is None else datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%d %H:%M")


def _day(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%d")
