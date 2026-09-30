"""`load`: synthetic calls held against a gateway exactly as workers hold them, and measured."""

import argparse
import asyncio
import math
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.errors import DeclarationRefused, GatewayRefused
from pinecall.domain.names import SANDBOX, JsonObject
from pinecall.fleet.client import SEALED_WITHIN_S, TIMEOUT_S, GatewayClient
from pinecall.process.settings import Settings
from pinecall.wire.events import CallEnded
from pinecall.wire.frames import Entry
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest

# What the gateway writes on a call's log itself, never a worker: the arrival, call.attached and
# call.claimed, the summary and the score, memory and sources, a tool's round trip, and the
# markers a reader is sent and no store keeps.
GATEWAY_KINDS = frozenset(
    {
        "call.ringing",
        "call.dialing",
        "call.attached",
        "call.claimed",
        "call.summary",
        "call.score",
        "memory.ops",
        "docs.sources",
        "tool.call",
        "tool.result",
        "log.gap",
        "log.caught_up",
    }
)

ENDED = "call.ended"

# The channel needs no number: the load names no carrier.
CHANNEL = "web"

CALLER = "load"

OUTCOME = "load"

# Between a refusal and the next call, so a gateway that refuses every open is not hammered.
REFUSED_PAUSE_S = 1.0

LAG_EVERY_S = 0.1

# Over this, the generator measures its own loop and not the gateway.
LAG_WARNING_MS = 50.0

# Past the run's end, what a call still writing and sealing is given before it is cut.
GRACE_S = 2 * SEALED_WITHIN_S

UNREACHABLE = "unreachable"

NO_KEY = "PINECALL_WORKER_KEY is unset: the load knocks with the sandbox fleet's key"

NOT_A_SCRIPT = "--script {path}: {why}"

NOT_ENOUGH = "--calls {calls}: at least one"

_ENTRIES: TypeAdapter[list[Entry]] = TypeAdapter(list[Entry])


@dataclass(frozen=True)
class Step:
    """One entry of the script: how long after the call opened it is written, and what it is."""

    at_s: float
    type: str
    data: JsonObject
    ephemeral: bool


@dataclass(frozen=True)
class Script:
    """What one synthetic call writes: the worker's entries in order, then its call.ended."""

    steps: tuple[Step, ...]
    ending: Step


@dataclass(frozen=True)
class Plan:
    """A run: the org and agent the calls are opened for, the script, and the curve."""

    org: str
    agent: str
    script: Script
    calls: int
    ramp_s: float
    hold_s: float


@dataclass(frozen=True)
class Window:
    """When a run reaches its top and when it ends, on the monotonic clock."""

    top_at: float
    ends_at: float


@dataclass
class Tally:
    """What a run counted while it ran: calls, entries, latencies, refusals, logs, the loop."""

    opened: int = 0
    sealed: int = 0
    held: int = 0
    most_held: int = 0
    sent: int = 0
    durable: int = 0
    at_top: int = 0
    append_ms: list[float] = field(default_factory=list[float])
    seal_ms: list[float] = field(default_factory=list[float])
    refusals: Counter[str] = field(default_factory=Counter[str])
    verified: int = 0
    wrong: int = 0
    unread: Counter[str] = field(default_factory=Counter[str])
    lag_ms: list[float] = field(default_factory=list[float])


@dataclass(frozen=True)
class Run:
    """What every call of a run shares: the client, the plan, the tally and the window."""

    client: GatewayClient
    plan: Plan
    tally: Tally
    window: Window


def load_verb(verb: argparse.ArgumentParser) -> None:
    """`load --org --agent --script --calls --ramp --minutes`."""
    verb.add_argument("--org", required=True, help="the org the calls are opened for")
    verb.add_argument("--agent", required=True, help="the agent the calls are opened for")
    verb.add_argument("--script", required=True, help="a call's log as JSON, replayed per call")
    verb.add_argument("--calls", required=True, type=int, help="how many at once at the top")
    verb.add_argument("--ramp", required=True, type=float, help="seconds to reach them, linearly")
    verb.add_argument("--minutes", required=True, type=float, help="how long the top is held")
    verb.set_defaults(run=loaded)


def loaded(settings: Settings, args: argparse.Namespace) -> int:
    """Hold the calls against the gateway, then print what was measured."""
    if settings.worker_key is None:
        raise DeclarationRefused(NO_KEY)
    if args.calls < 1:
        raise DeclarationRefused(NOT_ENOUGH.format(calls=args.calls))
    plan = Plan(
        org=args.org,
        agent=args.agent,
        script=script_of(_entries_in(Path(args.script))),
        calls=args.calls,
        ramp_s=args.ramp,
        hold_s=args.minutes * 60,
    )
    tally = asyncio.run(_measured(settings.gateway_url, settings.worker_key, plan))
    sys.stdout.writelines(f"{line}\n" for line in report_of(tally, plan.hold_s))
    return 0


def written_by_worker(kind: str) -> bool:
    """Whether a worker writes this kind of entry, rather than the gateway itself."""
    return kind not in GATEWAY_KINDS


# ts may step back between two entries of a real log: a step is never earlier than the last.
def script_of(entries: Sequence[Entry]) -> Script:
    """The worker's entries of a call's log, timed from its first; call.ended made when absent."""
    first = entries[0].ts
    steps: list[Step] = []
    ending: Step | None = None
    at_s = 0.0
    for entry in entries:
        at_s = max(at_s, entry.ts - first)
        if not written_by_worker(entry.type):
            continue
        step = Step(at_s, entry.type, dict(entry.data), entry.ephemeral)
        if entry.type == ENDED:
            ending = step
        else:
            steps.append(step)
    if ending is None:
        over = CallEnded(
            reason="caller_hung_up", ended_by="caller", ended_at=first + at_s, duration_s=at_s
        )
        ending = Step(at_s, ENDED, dict(over.written()), ephemeral=False)
    return Script(tuple(steps), ending)


def starts_of(calls: int, ramp_s: float) -> list[float]:
    """When each slot starts, in seconds from the run's start: evenly along the ramp."""
    return [ramp_s * slot / calls for slot in range(calls)]


def percentile(samples: Sequence[float], share: float) -> float | None:
    """The nearest-rank percentile of the samples (share 0.99 for p99); None for no sample."""
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(math.ceil(share * len(ordered)) - 1, 0)]


def rising(sent: Sequence[Entry]) -> bool:
    """Whether the seqs the gateway gave a call's entries rise strictly, in the order sent."""
    return all(before.seq < after.seq for before, after in pairwise(sent))


# The kinds sent are compared: a duplicate from a retry is an entry of a sent kind at a new seq.
def log_holds(sent: Sequence[Entry], read: Sequence[Entry]) -> bool:
    """Whether a log read back holds every durable entry sent, once each, in order, and no other."""
    kinds = {entry.type for entry in sent}
    written = [(entry.seq, entry.type) for entry in sent if not entry.ephemeral]
    return [(entry.seq, entry.type) for entry in read if entry.type in kinds] == written


async def run_load(client: GatewayClient, plan: Plan) -> Tally:
    """Hold the plan's calls on the client until the run ends, and count what happened."""
    tally = Tally()
    began = time.monotonic()
    window = Window(top_at=began + plan.ramp_s, ends_at=began + plan.ramp_s + plan.hold_s)
    stopped = asyncio.Event()
    try:
        async with (
            asyncio.timeout(plan.ramp_s + plan.hold_s + GRACE_S),
            asyncio.TaskGroup() as group,
        ):
            group.create_task(_lag(tally, stopped))
            run = Run(client, plan, tally, window)
            slots = [
                group.create_task(_slot(run, start)) for start in starts_of(plan.calls, plan.ramp_s)
            ]
            await asyncio.wait(slots)
            stopped.set()
    except TimeoutError:
        # Past the grace, a call still writing is cut; the report says how many were open.
        pass
    return tally


def report_of(tally: Tally, hold_s: float) -> list[str]:
    """What a run measured, a line each, as a person and a script both read it."""
    per_second = "-" if hold_s <= 0 else f"{tally.at_top / hold_s:.1f}"
    lag = percentile(tally.lag_ms, 0.99)
    lines = [
        f"calls opened: {tally.opened}",
        f"calls sealed: {tally.sealed}",
        f"calls open at the end: {tally.held}",
        f"most held at once: {tally.most_held}",
        f"entries sent: {tally.sent} ({tally.durable} durable)",
        f"entries a second at the top: {per_second}",
        f"append ms: {_percentiles(tally.append_ms, (0.5, 0.95, 0.99))}",
        f"seal ms: {_percentiles(tally.seal_ms, (0.5, 0.99))}",
        f"refusals: {_counted(tally.refusals)}",
        f"logs verified: {tally.verified}",
        f"logs found wrong: {tally.wrong}",
        f"logs unread: {sum(tally.unread.values())} (refused: {_counted(tally.unread)})",
        f"loop lag ms: p99 {_ms(lag)}",
    ]
    if lag is not None and lag > LAG_WARNING_MS:
        lines.append(
            f"warning: this generator's own loop lagged {lag:.1f} ms at p99, over "
            f"{LAG_WARNING_MS:.0f}: it measured itself, not the gateway; run fewer calls a process"
        )
    return lines


async def _measured(url: str, key: str, plan: Plan) -> Tally:
    # One request in flight per call at most: a pool of --calls never makes a call wait on it.
    limits = httpx.Limits(max_connections=plan.calls, max_keepalive_connections=plan.calls)
    http = httpx.AsyncClient(
        base_url=url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=TIMEOUT_S,
        limits=limits,
    )
    client = GatewayClient(http)
    try:
        return await run_load(client, plan)
    finally:
        await client.aclose()


def _entries_in(path: Path) -> list[Entry]:
    try:
        entries = _ENTRIES.validate_json(path.read_bytes())
    except (OSError, ValueError) as unread:
        raise DeclarationRefused(NOT_A_SCRIPT.format(path=path, why=unread)) from None
    if not entries:
        raise DeclarationRefused(NOT_A_SCRIPT.format(path=path, why="the log holds no entry"))
    return entries


async def _lag(tally: Tally, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        before = time.monotonic()
        await asyncio.sleep(LAG_EVERY_S)
        tally.lag_ms.append((time.monotonic() - before - LAG_EVERY_S) * 1000)


# A refusal ends that call, never the slot: the slot opens a fresh call after a pause.
async def _slot(run: Run, start_s: float) -> None:
    await asyncio.sleep(start_s)
    while time.monotonic() < run.window.ends_at:
        try:
            await _call(run)
        except GatewayRefused as refused:
            run.tally.refusals[_status(refused)] += 1
            await asyncio.sleep(REFUSED_PAUSE_S)


async def _call(run: Run) -> None:
    plan, tally = run.plan, run.tally
    context = CallContext(
        call=new_call_id(),
        channel=CHANNEL,
        direction="inbound",
        caller=CALLER,
        route=Route(org=plan.org, agent=plan.agent, channel=CHANNEL, env=SANDBOX),
        today=datetime.now(UTC).date(),
    )
    await run.client.open(OpenCallRequest(agent=plan.agent, context=context))
    tally.opened += 1
    tally.held += 1
    tally.most_held = max(tally.most_held, tally.held)
    try:
        sent = await _replayed(run, context.call)
        started = time.monotonic()
        await run.client.sealed(context.call, SealCallRequest(usage=[], outcome=OUTCOME))
        tally.seal_ms.append((time.monotonic() - started) * 1000)
        tally.sealed += 1
    finally:
        tally.held -= 1
    await _verified(run, context.call, sent)


# At the script's own times from the open, one request in flight, as the worker's Writing sends;
# a call the run's end overtakes skips to its call.ended.
async def _replayed(run: Run, call: str) -> list[Entry]:
    script, ends_at = run.plan.script, run.window.ends_at
    opened_at = time.monotonic()
    sent: list[Entry] = []
    for step in (*script.steps, script.ending):
        due = opened_at + step.at_s
        if due >= ends_at and step is not script.ending:
            continue
        await asyncio.sleep(max(min(due, ends_at) - time.monotonic(), 0.0))
        sent.append(await _sent(run, call, step))
    return sent


async def _sent(run: Run, call: str, step: Step) -> Entry:
    tally = run.tally
    started = time.monotonic()
    entry = await run.client.append(call, step.type, dict(step.data), ephemeral=step.ephemeral)
    done = time.monotonic()
    tally.append_ms.append((done - started) * 1000)
    tally.sent += 1
    tally.durable += 0 if entry.ephemeral else 1
    if run.window.top_at <= done <= run.window.ends_at:
        tally.at_top += 1
    return entry


# Read through the log's own door as a reader pages it; a key that may not read is counted apart.
async def _verified(run: Run, call: str, sent: list[Entry]) -> None:
    tally = run.tally
    if not rising(sent):
        tally.wrong += 1
        return
    try:
        read = [entry async for entry in run.client.since(call, 0)]
    except GatewayRefused as refused:
        tally.unread[_status(refused)] += 1
        return
    if log_holds(sent, read):
        tally.verified += 1
    else:
        tally.wrong += 1


def _status(refused: GatewayRefused) -> str:
    return UNREACHABLE if refused.answered is None else str(refused.answered)


def _percentiles(samples: Sequence[float], shares: Sequence[float]) -> str:
    return " ".join(f"p{round(share * 100)} {_ms(percentile(samples, share))}" for share in shares)


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _counted(counts: Counter[str]) -> str:
    return " ".join(f"{name}={count}" for name, count in sorted(counts.items())) or "none"
