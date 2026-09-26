"""Reading a log while it is being written: the backlog, the marker, then live — and the gap."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable

from pinecall.log.entry import Entry
from pinecall.log.fanout import Fanout
from pinecall.log.filters import EVERYTHING, Filter
from pinecall.log.reduce import reduce
from pinecall.log.store.protocol import DEFAULT_LIMIT, Store
from pinecall_protocol import encode
from pinecall_protocol.events import LogCaughtUp, LogGap
from pinecall_protocol.registry import TERMINAL_EVENT


async def stream(
    store: Store,
    fanout: Fanout,
    call: str,
    *,
    after: int = 0,
    filter: Filter = EVERYTHING,
    limit: int = DEFAULT_LIMIT,
) -> AsyncIterator[Entry]:
    """Everything above the cursor, then live. Subscribe first: the seam must not lose an entry."""
    cursor = after
    agent = ""
    while True:
        # The subscription opens before the first page is read, so an entry written during the
        # backlog lands in the queue instead of falling between the two halves of the stream.
        # The live half then skips whatever the backlog already sent; the cursor decides, not luck.
        subscription = fanout.subscribe(filter)
        try:
            async for entry in pages(_a_call(store, call), cursor, limit):
                agent, cursor = entry.agent, entry.seq
                if filter.passes(entry):
                    yield entry
            yield caught_up(call, agent, cursor)
            async for entry in subscription:
                if entry.seq <= cursor:
                    continue
                agent, cursor = entry.agent, entry.seq
                yield entry
            if not subscription.dropped:
                return
        finally:
            subscription.close()
        # It fell behind the hot window. What it missed is in the store, but handing back a
        # truncated list as if it were whole is the lie the references told: say gap, and say it
        # with the state as of now, so one entry catches the reader up instead of a thousand.
        missed = await gap(store, call, agent, cursor)
        cursor = missed.data["to_seq"]
        yield missed


# One page of some log, above a cursor: a call's or an agent's. The tail does not care which.
type Pager = Callable[[int, int], Awaitable[list[Entry]]]


# The one page-until-a-short-page loop of the runtime: this stream reads its backlog through it,
# whole() below is how everybody else reads a finished call, and the meter folds every org's
# usage through it with a position for a cursor. Written three times once, and three times is
# where two of them start disagreeing about the last page.
async def pages[T](
    read: Callable[[int, int], Awaitable[list[T]]],
    after: int,
    limit: int,
    cursor_of: Callable[[T], int] = lambda entry: entry.seq,  # type: ignore[attr-defined]
) -> AsyncIterator[T]:
    """Page through what the store has, oldest first, until a short page says that was all."""
    cursor = after
    while True:
        page = await read(cursor, limit)
        for entry in page:
            yield entry
        if len(page) < limit:
            return
        cursor = cursor_of(page[-1])


# Four readers ask for a whole call: the gap that carries a snapshot, the state door, the CLI's
# `sessions show` and the eval's replay. A store answers in pages, so the paging is theirs to share.
async def whole(
    store: Store, call: str, *, after: int = 0, limit: int = DEFAULT_LIMIT
) -> list[Entry]:
    """Every durable entry of one call above the cursor, oldest first, page by page."""
    return [entry async for entry in pages(_a_call(store, call), after, limit)]


def _a_call(store: Store, call: str) -> Pager:
    """One call's pages, as the tail reads them."""
    return lambda after, limit: store.since(call, after=after, limit=limit)


def _an_agent(store: Store, agent: str) -> Pager:
    """One agent's own pages, the same way."""
    return lambda after, limit: store.agent_since(agent, after=after, limit=limit)


def caught_up(call: str | None, agent: str, seq: int) -> Entry:
    """The boundary: everything up to seq has been sent and what follows is live. Never stored."""
    return _marker(call, agent, seq, "log.caught_up", encode(LogCaughtUp(seq=seq)))


async def gap(
    store: Store, call: str, agent: str, cursor: int, limit: int = DEFAULT_LIMIT
) -> Entry:
    """What the reader missed, and the state it missed it from. Stands at the seq it speaks for."""
    entries = await whole(store, call, limit=limit)
    to_seq = max(await store.latest_seq(call), cursor)
    snapshot = reduce(entries)
    data = encode(LogGap(from_seq=cursor + 1, to_seq=to_seq, snapshot=snapshot))
    return _marker(call, agent, to_seq, "log.gap", data)


def _marker(call: str | None, agent: str, seq: int, type: str, data: dict[str, object]) -> Entry:
    """A marker is an entry about the stream, not about the call: ephemeral, and never appended."""
    return Entry(
        seq=seq, ts=time.time(), call=call, agent=agent, type=type, ephemeral=True, data=data
    )


# The agent's own log is the other half of the protocol's reader: `agent.registered`,
# `agent.configured`, an `error` outside a call. It is the same tail with two differences, and both
# are why this is a second function and not a flag: it never ends, because an agent's log has no
# end, and its gap carries no snapshot, because an agent log reduces to no state — there is nothing
# to catch up TO, only entries the store still has and hands back on the next page.
async def agent_stream(
    store: Store,
    fanout: Fanout,
    agent: str,
    *,
    after: int = 0,
    filter: Filter = EVERYTHING,
    limit: int = DEFAULT_LIMIT,
) -> AsyncIterator[Entry]:
    """The agent's entries above the cursor, log.caught_up, then live. Subscribe first, as ever."""
    cursor = after
    read = _an_agent(store, agent)
    while True:
        subscription = fanout.subscribe(filter)
        try:
            async for entry in pages(read, cursor, limit):
                cursor = entry.seq
                if filter.passes(entry):
                    yield entry
            yield caught_up(None, agent, cursor)
            async for entry in subscription:
                if entry.seq <= cursor:
                    continue
                cursor = entry.seq
                yield entry
            if not subscription.dropped:
                return
        finally:
            subscription.close()
        # It fell behind. Say gap — the reader is told it missed something — and then read on from
        # the same cursor: the durable entries are still in the store, only ephemerals are gone.
        yield _marker(
            None,
            agent,
            cursor,
            "log.gap",
            encode(LogGap(from_seq=cursor + 1, to_seq=cursor, snapshot=None)),
        )


# The Store keeps no flag to ask, on purpose: what ends a call is the protocol's terminal event,
# and the store must not have to read the protocol to write a row. So the tail is the answer.
async def is_sealed(store: Store, call: str) -> bool:
    """Whether this call's log is sealed: its last entry is the terminal one, or it is not over."""
    latest = await store.latest_seq(call)
    if latest == 0:
        return False
    tail = await store.since(call, after=latest - 1, limit=1)
    return bool(tail) and tail[-1].type == TERMINAL_EVENT
