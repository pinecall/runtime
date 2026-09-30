"""The logs open in this process: append, the live readers, replay, the feeds."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Self

from pinecall.domain.call import CallContext
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import ENVS, Env, JsonObject
from pinecall.log.readers import EVERYTHING, Filter
from pinecall.log.reduce import reduce
from pinecall.log.store import Claimant, Store, Unnumbered, log_name
from pinecall.wire.events import (
    EPHEMERAL_EVENTS,
    TERMINAL_EVENT,
    CallDialing,
    CallRinging,
    CallStarted,
)
from pinecall.wire.frames import Entry
from pinecall.wire.parts import Route
from pinecall.wire.rest.calls import BatchedEntry
from pinecall.wire.state import State

# Runs inline on every append, before append returns: for work that is part of the write.
type Tap = Callable[[Entry], Awaitable[None]]


# How far a reader may fall behind before it is dropped: about a minute of a busy call, and the
# most a dead socket can pin.
QUEUE_DEPTH = 256


# What an org's feed and the box's carry: the lifecycle, never the content of a call.
ORG_EVENTS = frozenset(
    {
        "agent.registered",
        "agent.detached",
        "call.ringing",
        "call.dialing",
        "call.started",
        "call.ended",
        "attention.requested",
        "attention.answered",
        "supervisor.took_over",
        "supervisor.released",
    }
)


class Subscription:
    """One live reader's queue; iteration ends when the log closes or the reader fell behind."""

    def __init__(self, only: Filter, leave: Callable[["Subscription"], None]) -> None:
        """Keep the filter and the way out of the fanout."""
        self.only = only
        self.dropped = False
        self._leave = leave
        self._ended = False
        # One slot past the depth for the end marker, so closing a full queue never blocks.
        self._queue: asyncio.Queue[Entry | None] = asyncio.Queue(maxsize=QUEUE_DEPTH + 1)

    def offer(self, entry: Entry) -> bool:
        """Queue the entry if the filter passes it; False once this reader is gone."""
        if self._ended:
            return False
        if not self.only.passes(entry):
            return True
        if self._queue.qsize() >= QUEUE_DEPTH:
            self.dropped = True
            self._end()
            return False
        self._queue.put_nowait(entry)
        return True

    def close(self) -> None:
        """End the iteration after what is queued, and leave the fanout."""
        self._end()
        self._leave(self)

    def _end(self) -> None:
        if not self._ended:
            self._ended = True
            self._queue.put_nowait(None)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Entry:
        """Return the next entry in log order; the end marker ends the iteration."""
        entry = await self._queue.get()
        if entry is None:
            raise StopAsyncIteration
        return entry


class Fanout:
    """The live readers of one log or feed; publishing never waits for any of them."""

    def __init__(self) -> None:
        """Start with no reader."""
        self._readers: set[Subscription] = set()
        self._closed = False

    @property
    def readers(self) -> int:
        """Return how many readers are subscribed."""
        return len(self._readers)

    def subscribe(self, only: Filter = EVERYTHING) -> Subscription:
        """Return a reader of what is published from now on; on a closed fanout, an ended one."""
        subscription = Subscription(only, self._readers.discard)
        if self._closed:
            subscription.close()
        else:
            self._readers.add(subscription)
        return subscription

    def publish(self, entry: Entry) -> None:
        """Offer the entry to every reader, dropping the ones that fell behind."""
        for reader in tuple(self._readers):
            if not reader.offer(entry):
                self._readers.discard(reader)

    def close(self) -> None:
        """End every reader after what it was given; a later subscriber gets nothing."""
        self._closed = True
        for reader in tuple(self._readers):
            reader.close()
        self._readers.clear()


# Readers and the writer share the one Log of a name: a reader often arrives before the writer.
class Log:
    """One call's log, or one agent's: written here, read live here, replayed from the store."""

    def __init__(self, store: Store, call: str | None, agent: str) -> None:
        """Name the log: a call's, or an agent's own when call is None."""
        self.call = call
        self.agent = agent
        self.name = log_name(call, agent)
        self.fanout = Fanout()
        self.sealed = False
        # Set by Logs.writing: a held log outlives its readers until the process forgets it.
        self.kept_open = False
        self._store = store
        self._taps: list[Tap] = []
        self._snapshot: tuple[int, State] | None = None

    def tapped(self, tap: Tap) -> None:
        """Run the tap on every append from now on, before append returns."""
        self._taps.append(tap)

    # Stored before published, so no reader sees an entry the store lacks and every cursor resumes.
    async def append(
        self, kind: str, data: JsonObject | None = None, *, ephemeral: bool | None = None
    ) -> Entry:
        """Write the entry, publish it, run the taps, and seal the log on the terminal event."""
        forgettable = kind in EPHEMERAL_EVENTS if ephemeral is None else ephemeral
        entry = await self._store.append(
            self.call, self.agent, kind, data or {}, ephemeral=forgettable
        )
        await self._published(entry)
        return entry

    # Two writers deciding the same end (a webhook delivered twice) write it once.
    async def append_first(self, kind: str, data: JsonObject) -> Entry | None:
        """Write and publish a durable entry unless the log holds one of its type; None then."""
        if self.call is None:
            raise DeclarationRefused(f"agent {self.agent}: only a call's log is looked at")
        entry = await self._store.append_first(self.call, self.agent, kind, data)
        if entry is not None:
            await self._published(entry)
        return entry

    # A replayed batch was published when it was first taken: publishing it again would repeat it.
    async def append_many(self, entries: Sequence[BatchedEntry], *, after: int) -> list[Entry]:
        """Write a worker's batch once, then publish and tap each entry in order as append does."""
        unnumbered = [
            Unnumbered(
                type=item.type,
                data=item.data,
                ephemeral=item.type in EPHEMERAL_EVENTS
                if item.ephemeral is None
                else item.ephemeral,
                ts=item.ts,
            )
            for item in entries
        ]
        batch = await self._store.append_many(self.call, self.agent, unnumbered, after=after)
        if batch.replayed:
            return batch.entries
        for entry in batch.entries:
            await self._published(entry)
        return batch.entries

    async def seal(self) -> None:
        """Seal the call's log in the store and end every live reader."""
        if self.call is None:
            raise DeclarationRefused(f"agent {self.agent}: an agent's log has no end")
        self.sealed = True
        await self._store.seal(self.call)
        self.fanout.close()

    # Compared against the store's seq, ephemerals counted, or a live call never hits the memo.
    async def snapshot(self) -> State:
        """Return the state the log reduces to, folded again only when the log moved."""
        latest = await self._store.latest_seq(self.name)
        if self._snapshot is None or self._snapshot[0] != latest:
            self._snapshot = latest, reduce(await self._store.whole(self.name))
        return self._snapshot[1]

    async def stream(self, *, after: int = 0, only: Filter = EVERYTHING) -> AsyncIterator[Entry]:
        """Yield the backlog above the cursor, log.caught_up, then what is written from then on."""
        cursor = after
        while True:
            # Subscribed before the backlog is read, so nothing written between the two is lost.
            subscription = self.fanout.subscribe(only)
            try:
                # Asked before the backlog: a log sealed by then is whole in it, and one sealed
                # after ends its readers here, once they drained what they were given.
                over = self.sealed or await self._store.sealed(self.name)
                for entry in await self._store.whole(self.name, after=cursor):
                    cursor = entry.seq
                    if only.passes(entry):
                        yield entry
                yield self._marker(cursor, "log.caught_up", {"seq": cursor})
                if over:
                    return
                async for entry in subscription:
                    if entry.seq > cursor:
                        cursor = entry.seq
                        yield entry
                if not subscription.dropped:
                    return
            finally:
                subscription.close()
            # Fell behind: a gap says what was missed and, for a call, carries the state instead
            # of the entries. An agent's log reduces to no state, so its gap re-reads the store.
            if self.call is None:
                yield self._marker(
                    cursor, "log.gap", {"from_seq": cursor + 1, "to_seq": cursor, "snapshot": None}
                )
                continue
            to_seq = max(await self._store.latest_seq(self.name), cursor)
            snapshot = (await self.snapshot()).written()
            yield self._marker(
                to_seq, "log.gap", {"from_seq": cursor + 1, "to_seq": to_seq, "snapshot": snapshot}
            )
            cursor = to_seq

    async def _published(self, entry: Entry) -> None:
        self.fanout.publish(entry)
        for tap in list(self._taps):
            await tap(entry)
        if entry.type == TERMINAL_EVENT and self.call is not None:
            await self.seal()

    def _marker(self, seq: int, kind: str, data: JsonObject) -> Entry:
        return Entry(
            seq=seq,
            ts=time.time(),
            call=self.call,
            agent=self.agent,
            type=kind,
            ephemeral=True,
            data=data,
        )


class Logs:
    """The logs of this process by name, the org feeds and the box's, and who owns what."""

    def __init__(self, store: Store) -> None:
        """Start with no log open and no feed listened to."""
        self.store = store
        self.box = Fanout()
        self._logs: dict[str, Log] = {}
        self._feeds: dict[tuple[str, Env], Fanout] = {}
        # A log's owner never changes; caching it keeps a store read off the append path.
        self._owners: dict[str, Claimant] = {}

    def writing(self, call: str, agent: str) -> Log:
        """Return the call's log for its writer, held until the process forgets the call."""
        log = self._log(call, agent)
        log = self._log(call, agent)
        log.agent = agent
        if not log.kept_open:
            log.kept_open = True
            log.tapped(self._fed)
        return log

    def reading(self, call: str) -> Log:
        """Return the call's log for a reader, whoever writes it."""
        self._prune(but=call)
        return self._log(call, "")

    def agent(self, slug: str) -> Log:
        """Return the agent's own log, for writing or reading."""
        self._prune(but=log_name(None, slug))
        log = self._log(None, slug)
        if not log.kept_open:
            log.kept_open = True
            log.tapped(self._fed)
        return log

    def opened(self, call: str) -> Log | None:
        """Return the call's log if this process writes it."""
        log = self._logs.get(call)
        return log if log is not None and log.kept_open else None

    def forget(self, call: str) -> None:
        """Drop the call's log, its readers and its cached owner."""
        self._logs.pop(call, None)
        self._owners.pop(call, None)

    def feed(self, org: str, env: Env) -> Fanout:
        """Return the org's feed in that world: the lifecycle entries of every log it owns there."""
        self._prune()
        feed = self._feeds.get((org, env))
        if feed is None:
            feed = self._feeds[(org, env)] = Fanout()
        return feed

    # Only a found owner is cached: a log nobody claimed yet may be claimed later.
    async def claimant_of(self, entry: Entry) -> Claimant | None:
        """Return whose the entry's log is and in which world, asked of the store once per log."""
        name = log_name(entry.call, entry.agent)
        found = self._owners.get(name)
        if found is None:
            found = await self.store.claimant(entry.call, entry.agent)
            if found is not None:
                self._owners[name] = found
        return found

    def _log(self, call: str | None, agent: str) -> Log:
        name = log_name(call, agent)
        log = self._logs.get(name)
        if log is None:
            log = self._logs[name] = Log(self.store, call, agent)
        return log

    # An agent's own entries serve both worlds, so they reach the org's feed in each.
    async def _fed(self, entry: Entry) -> None:
        if entry.type not in ORG_EVENTS:
            return
        claimant = await self.claimant_of(entry)
        if claimant is None:
            return
        self.box.publish(entry)
        worlds = ENVS if claimant.env is None else (claimant.env,)
        for env in worlds:
            feed = self._feeds.get((claimant.org, env))
            if feed is not None:
                feed.publish(entry)

    # On every read, so readers asking for arbitrary names cannot grow the tables without bound.
    def _prune(self, *, but: str = "") -> None:
        idle = [
            name
            for name, log in self._logs.items()
            if name != but and not log.kept_open and not log.fanout.readers
        ]
        for name in idle:
            del self._logs[name]
        for key in [k for k, feed in self._feeds.items() if not feed.readers]:
            del self._feeds[key]


# The caller is always the far end: on an outbound call that is the `to`.
def arrival_entry(
    context: CallContext, door: str, *, asked_by: str | None = None
) -> tuple[str, JsonObject]:
    """Return a call's first entry: call.dialing when placed, call.ringing when offered."""
    from_, to = _two_ends(context, door)
    data: JsonObject = {
        "channel": context.channel,
        "from": from_,
        "to": to,
        "run": context.run,
        "caller": None,
    }
    if context.direction == "outbound":
        # The one record of who placed an outbound call, which is what audits dial spend.
        dialing = CallDialing.read({**data, "asked_by": asked_by}, "call.dialing")
        return "call.dialing", dialing.written()
    route = Route(channel=context.route.channel, number=context.route.number)
    ringing = CallRinging.read({**data, "route": route.written()}, "call.ringing")
    return "call.ringing", ringing.written()


def started_entry(context: CallContext, door: str, at: float) -> JsonObject:
    """Return the call.started entry, written once media is up."""
    from_, to = _two_ends(context, door)
    started = CallStarted.read(
        {
            "channel": context.channel,
            "direction": context.direction,
            "from": from_,
            "to": to,
            "run": context.run,
            # The persona and its rules, frozen here for the facts and the persona judge.
            "persona": context.persona,
            "accepts_when": context.accepts_when,
            "declines_when": context.declines_when,
            "caller": None,
            "started_at": at,
            "env": context.env,
        },
        "call.started",
    )
    return started.written()


def _two_ends(context: CallContext, door: str) -> tuple[str, str]:
    if context.direction == "outbound":
        return door, context.caller
    return context.caller, door
