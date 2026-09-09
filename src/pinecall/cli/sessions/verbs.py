"""`pinecall-runtime sessions`: the log read back — list, show, tail, recording. Off Postgres."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import Any, TextIO

from pinecall._settings import load_settings
from pinecall.cli.columns import as_columns
from pinecall.cli.sessions import render
from pinecall.cli.sessions.source import Calls, Source
from pinecall.log.entry import Entry
from pinecall.log.latencies import medians
from pinecall.log.reduce import reduce
from pinecall_protocol import encode
from pinecall_protocol.registry import TERMINAL_EVENT
from pinecall_protocol.state import State

PURPOSE: str = "the log, read back: list | show | tail | recording"
VERBS: tuple[str, ...] = ("list", "show", "tail", "recording")

# How many calls `list` shows when nobody says. A screen's worth — not the store's page size,
# which is a different number for a different reason and lives in log/store/protocol.py.
A_SCREENFUL = 20

# The gateway fans an entry out to every reader inside its own process; the CLI is another
# process, on another machine as often as not, so no fanout can reach it. It polls the store
# instead, four times a second: fast enough that a reply appears while the caller is still
# speaking, slow enough that following a call costs the database four small queries a second.
POLL_SECONDS = 0.25

# What a call that never reached a summary shows in the cost column: a number would be a lie, and
# 0.00 the worst kind of one.
UNPRICED = "unpriced"

NOTHING = "—"

# An id nobody wrote under. Said once, because `show` and `recording` are asked the same typo and
# a person who greps for one of the two answers must find the other.
NO_SUCH_CALL = "no call {call} in the log"

# The path a recording is filed at travels in the call's summary and nowhere else: the worker
# composes it (worker/recordings.py) and the log states it there, once, near the end. It is NOT
# the terminal entry — `call.score` is, and `tail` stops on that one — so the two are named apart.
THE_SUMMARY = "call.summary"
NOT_SEALED = "call {call} has no {summary} yet: the recording is stated when the call ends"
NOT_RECORDED = "call {call} was not recorded: its {summary} carries no path"


def configure(parser: argparse.ArgumentParser) -> None:
    """Four verbs, four parsers: each names its arguments, and `sessions` alone prints them."""
    verbs = parser.add_subparsers(title="verbs", metavar="<verb>", prog=f"{parser.prog} sessions")

    listing = verbs.add_parser("list", help="the calls, newest first")
    listing.add_argument("--agent", help="only this agent's calls")
    listing.add_argument("--limit", type=int, default=A_SCREENFUL, help="how many (default 20)")
    listing.set_defaults(run=run_list)

    showing = verbs.add_parser("show", help="one call, entry by entry")
    showing.add_argument("call", metavar="<id>", help="the call id")
    showing.add_argument("--json", action="store_true", help="print the reduced state instead")
    showing.set_defaults(run=run_show)

    following = verbs.add_parser("tail", help="follow a call as it happens")
    following.add_argument("call", metavar="<id>", nargs="?", help="default: the newest live call")
    following.set_defaults(run=run_tail)

    recorded = verbs.add_parser("recording", help="where this call's audio was written")
    recorded.add_argument("call", metavar="<id>", help="the call id")
    recorded.set_defaults(run=run_recording)

    parser.set_defaults(run=partial(_print_the_verbs, parser))


def run_list(arguments: argparse.Namespace) -> int:
    """The calls, newest first: what each was, how long, how it went, and what it cost."""
    return _against_the_database(partial(list_calls, arguments.agent, arguments.limit))


def run_show(arguments: argparse.Namespace) -> int:
    """One call whole: the reduced state as JSON, or the transcript and its medians."""
    return _against_the_database(partial(show_call, arguments.call, as_json=arguments.json))


def run_tail(arguments: argparse.Namespace) -> int:
    """Follow a call. Ctrl+C is an answer, not a crash: the traceback stops here, and so do we."""
    try:
        return _against_the_database(partial(tail_call, arguments.call))
    except KeyboardInterrupt:
        return 0


def run_recording(arguments: argparse.Namespace) -> int:
    """The path this call's audio is at, alone on a line, so `-o $(…)` is the whole download."""
    return _against_the_database(partial(recording_of, arguments.call))


# ── the verbs, as coroutines over a Source a test can hand in ───────────────────


async def list_calls(agent: str | None, limit: int, source: Calls, out: TextIO = sys.stdout) -> int:
    """Every call as one line. Nothing to list is a sentence, not an empty screen."""
    calls = await source.calls(agent, limit)
    if not calls:
        print(f"no calls yet{f' for {agent}' if agent else ''}", file=out)
        return 0
    rows = [_row_of(call, reduce(await source.entries(call))) for call in calls]
    for line in as_columns(rows):
        print(line, file=out)
    return 0


async def show_call(
    call: str, source: Calls, out: TextIO = sys.stdout, *, as_json: bool = False
) -> int:
    """One call, whole. An id nobody wrote under is an error, so a typo is never silence."""
    entries = await source.entries(call)
    if not entries:
        print(NO_SUCH_CALL.format(call=call), file=out)
        return 1
    if as_json:
        print(_as_json(reduce(entries)), file=out)
        return 0
    for line in [*render.transcript(entries), *render.medians_table(medians(entries))]:
        print(line, file=out)
    return 0


async def tail_call(
    call: str | None,
    source: Calls,
    out: TextIO = sys.stdout,
    *,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """The call from its first entry, then whatever arrives, until the summary or Ctrl+C."""
    following = call or await source.newest_live_call()
    if following is None:
        print("no live call to follow", file=out)
        return 1
    origin: float | None = None
    cursor = 0
    while True:
        arrived = await source.entries(following, after=cursor)
        if arrived:
            origin = origin if origin is not None else arrived[0].ts
            cursor = arrived[-1].seq
            for entry in arrived:
                for line in render.lines_of(entry, origin):
                    print(line, file=out)
        if any(entry.type == TERMINAL_EVENT for entry in arrived):
            return 0
        await sleep(poll)


async def recording_of(call: str, source: Calls, out: TextIO = sys.stdout) -> int:
    """The recording's path and nothing else on the line: a verb whose output is an argument."""
    entries = await source.entries(call)
    if not entries:
        print(NO_SUCH_CALL.format(call=call), file=out)
        return 1
    summary = _last_summary(entries)
    if summary is None:
        print(NOT_SEALED.format(call=call, summary=THE_SUMMARY), file=out)
        return 1
    path = summary.data.get("recording")
    if not isinstance(path, str) or not path:
        print(NOT_RECORDED.format(call=call, summary=THE_SUMMARY), file=out)
        return 1
    print(path, file=out)
    return 0


def _last_summary(entries: Sequence[Entry]) -> Entry | None:
    """The call's summary, read from the end: it is the log's last entry but one when it ended."""
    return next((entry for entry in reversed(entries) if entry.type == THE_SUMMARY), None)


# ── the list's one line ─────────────────────────────────────────────────────────


def _row_of(call: str, state: State) -> tuple[str, ...]:
    """A call as a person reads it: what it was, when, how long, how it went, what it cost."""
    return (
        call,
        state.channel or NOTHING,
        _started(state.started_at),
        _duration(state),
        _one_line(state.outcome),
        _cost(state),
    )


# An outcome is the agent's last words, and a model writes paragraphs: the list is one row per
# call, so the words are folded onto one line and cut where a terminal stops being readable.
OUTCOME_WIDTH = 72


def _one_line(outcome: str | None) -> str:
    """The outcome as a row can carry it: no newlines, and an ellipsis past the width."""
    if not outcome:
        return NOTHING
    words = " ".join(outcome.split())
    return words if len(words) <= OUTCOME_WIDTH else words[: OUTCOME_WIDTH - 1] + "…"


def _started(started_at: float | None) -> str:
    """UTC, to the second. A log is read from another timezone as often as not."""
    if started_at is None:
        return NOTHING
    return datetime.fromtimestamp(started_at, UTC).strftime("%Y-%m-%d %H:%M:%S")


def _duration(state: State) -> str:
    """From the call's own clocks. A call still running has no duration yet, and says so."""
    if state.started_at is None or state.ended_at is None:
        return NOTHING
    return f"{state.ended_at - state.started_at:.1f}s"


def _cost(state: State) -> str:
    """What the providers billed, in euros — or `unpriced` when the summary priced nothing."""
    if state.cost is None or not state.cost.rows:
        return UNPRICED
    return f"{state.cost.eur:.4f} EUR"


def _as_json(state: State) -> str:
    """The reduced state, indented, under the wire's own key names."""
    return json.dumps(encode(state), ensure_ascii=False, indent=2)


# ── the plumbing every verb shares ──────────────────────────────────────────────


def _against_the_database(verb: Callable[[Calls], Awaitable[int]]) -> int:
    """Open the database, run the verb, close it whatever happened. One loop per command."""
    try:
        return asyncio.run(_with_a_source(verb))
    except BrokenPipeError:
        # `sessions show | head` is how a transcript is read. The closed pipe is the reader saying
        # enough, so stdout goes to devnull and the interpreter's last flush has somewhere to land.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


async def _with_a_source(verb: Callable[[Calls], Awaitable[int]]) -> int:
    source = await Source.open(load_settings().database_url)
    try:
        return await verb(source)
    finally:
        await source.aclose()


def _print_the_verbs(parser: argparse.ArgumentParser, _arguments: Any) -> int:
    """`sessions` with no verb: say what there is, and exit as a help screen does."""
    parser.print_help()
    return 0
