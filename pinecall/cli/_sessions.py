"""The log read back off Postgres: the newest calls, one call whole or folded, a call followed."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from pinecall.domain.errors import NotFound
from pinecall.log import queries
from pinecall.log.logs import Logs
from pinecall.log.reduce import reduce
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool, open_pool
from pinecall.process.settings import Settings
from pinecall.tenancy import reads
from pinecall.tenancy.reads import Read
from pinecall.wire.events import TERMINAL_EVENT, CallSummary
from pinecall.wire.frames import Entry
from pinecall.wire.rest.calls import ReadKind

NO_SUCH_CALL = "no call {call} was ever written here"


NOT_RECORDED = "call {call} was not recorded: its call.summary carries no path"


NO_LIVE_CALL = "no call is open right now"


def sessions_group(group: argparse.ArgumentParser) -> None:
    """`sessions`: list, show, tail, recording."""
    sessions = group.add_subparsers(required=True)
    listing = sessions.add_parser("list", help="the newest calls")
    listing.add_argument("--agent", default=None)
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(run=sessions_list)
    show = sessions.add_parser("show", help="one call, entry by entry, or its state with --json")
    show.add_argument("call")
    show.add_argument("--json", dest="as_json", action="store_true")
    show.set_defaults(run=sessions_show)
    tail = sessions.add_parser("tail", help="a call as it happens; none named, the newest live")
    tail.add_argument("call", nargs="?", default=None)
    tail.set_defaults(run=sessions_tail)
    recording = sessions.add_parser("recording", help="where the call's audio was written")
    recording.add_argument("call")
    recording.set_defaults(run=sessions_recording)


def sessions_list(settings: Settings, args: argparse.Namespace) -> int:
    """The newest calls of the box, or of one agent: when, whose, how they ended."""
    return asyncio.run(_listed(settings, args.agent, args.limit))


def sessions_show(settings: Settings, args: argparse.Namespace) -> int:
    """One call's log printed whole, or the state it folds to as JSON."""
    if args.as_json:
        return asyncio.run(_folded(settings, args.call))
    return asyncio.run(_shown(settings, args.call))


def sessions_tail(settings: Settings, args: argparse.Namespace) -> int:
    """A call followed as it is written, until it seals."""
    return asyncio.run(_tailed(settings, args.call))


def sessions_recording(settings: Settings, args: argparse.Namespace) -> int:
    """The path the call's audio was written to; 1 when it kept none."""
    return asyncio.run(_recording(settings, args.call))


async def _listed(settings: Settings, agent: str | None, limit: int) -> int:
    pool = await open_pool(settings.database_url)
    try:
        store = Store(pool)
        calls = await store.newest_calls(limit, agent=agent)
        facts = await queries.facts_of_calls(pool, calls)
    finally:
        await pool.close()
    for call in calls:
        row = facts.get(call)
        if row is None:
            _line_out(call)
            continue
        _line_out(
            f"{call}  {row.agent}  {row.channel or '-':9}  {_when(row.ended_at)}"
            f"  {row.end_reason or 'open':16}  {row.outcome or ''}"
        )
    return 0


async def _shown(settings: Settings, call: str) -> int:
    for entry in await _whole(settings, call):
        _line_out(_line(entry))
    return 0


async def _folded(settings: Settings, call: str) -> int:
    entries = await _whole(settings, call)
    _line_out(json.dumps(reduce(entries).model_dump(mode="json", by_alias=True), indent=2))
    return 0


async def _tailed(settings: Settings, call: str | None) -> int:
    pool = await open_pool(settings.database_url)
    try:
        store = Store(pool)
        named = call or await store.newest_live_call()
        if named is None:
            raise NotFound(NO_LIVE_CALL)
        await _read_by_the_operator(pool, named, "log")
        async for entry in Logs(store).reading(named).stream():
            _line_out(_line(entry))
            if entry.type == TERMINAL_EVENT:
                return 0
    finally:
        await pool.close()
    return 0


async def _recording(settings: Settings, call: str) -> int:
    entries = await _whole(settings, call, "recording")
    summary = next((entry for entry in reversed(entries) if entry.type == "call.summary"), None)
    path = None if summary is None else CallSummary.model_validate(summary.data).recording
    if path is None:
        sys.stderr.write(f"{NOT_RECORDED.format(call=call)}\n")
        return 1
    _line_out(path)
    return 0


async def _whole(settings: Settings, call: str, what: ReadKind = "log") -> list[Entry]:
    pool = await open_pool(settings.database_url)
    try:
        entries = await Store(pool).whole(call)
        if entries:
            await _read_by_the_operator(pool, call, what)
    finally:
        await pool.close()
    if not entries:
        raise NotFound(NO_SUCH_CALL.format(call=call))
    return entries


# The operator reading an org's call off the box is a read the org's access log shows.
async def _read_by_the_operator(pool: Pool, call: str, what: ReadKind) -> None:
    kept = await queries.scope_of_call(pool, call)
    if kept is not None and kept.scope is not None:
        await reads.record(pool, kept.scope, Read(call, what, reads.OPERATOR))


def _line(entry: Entry) -> str:
    data = json.dumps(entry.data, ensure_ascii=False)
    return f"{entry.seq:>5}  {_when(entry.ts)}  {entry.type:24}  {data}"


def _when(at: float | None) -> str:
    if at is None:
        return "-" * 19
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%d %H:%M:%S")


def _line_out(text: str) -> None:
    sys.stdout.write(f"{text}\n")
