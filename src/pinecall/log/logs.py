"""The two logs a runtime writes: one per call, sealed when the call ends, and the agent's own."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from pinecall.log import replay
from pinecall.log.entry import Entry, ephemeral_by_default
from pinecall.log.fanout import Fanout, Subscription
from pinecall.log.filters import EVERYTHING, Filter
from pinecall.log.pii import Masker
from pinecall.log.store.protocol import DEFAULT_LIMIT, Store
from pinecall.types.json import JsonObject
from pinecall_protocol.registry import TERMINAL_EVENT

# A second listener on a log's appends, beside the fanout: what Logs uses to feed an org's own
# stream with the few entries that are about the org and not about one call. Async, because
# whose log it is lives on the store's head row.
type Tap = Callable[[Entry], Awaitable[None]]


class CallLog:
    """One call's log: appended through the store, published live, sealed after call.summary."""

    def __init__(
        self,
        store: Store,
        agent: str,
        call: str,
        *,
        masker: Masker | None = None,
        fanout: Fanout | None = None,
        tap: Tap | None = None,
    ) -> None:
        self._store = store
        self._agent = agent
        self._call = call
        self._masker = masker or Masker()
        self._fanout = fanout or Fanout()
        self._taps = [tap] if tap is not None else []
        self._sealed = False

    def tapped(self, tap: Tap) -> None:
        """Hear every entry of this log INLINE, before the append that wrote it returns.

        The fanout is the other way to read a log and the right one for a socket: a queue, drained
        by a task, at the reader's pace. A tap is for a reader whose work is part of the write —
        the WhatsApp thread's sender, whose door answers "said to the contact" — and for one that
        must see entries this session did not write itself: the gateway's lookups append
        `docs.sources` and `memory.ops` to this very log (`lookups/service.py`), and a reader fed
        from the session's own `emit` never saw them (production, 2026-09-20).
        """
        self._taps.append(tap)

    @property
    def call(self) -> str:
        """The call this log belongs to."""
        return self._call

    @property
    def sealed(self) -> bool:
        """True once call.summary went in, or seal() ran: nothing more is true of the call."""
        return self._sealed

    async def append(
        self, type: str, data: JsonObject | None = None, ephemeral: bool | None = None
    ) -> Entry:
        """Mask, write, publish, and — after the terminal entry — seal. In that order, always."""
        payload = self._masker.mask(type, data or {})
        forgettable = ephemeral_by_default(type) if ephemeral is None else ephemeral
        # The store numbers it and the row is written before anybody hears about it: a reader can
        # never see an entry the store does not have, which is what makes the cursor a promise.
        entry = await self._store.append(self._call, self._agent, type, payload, forgettable)
        self._fanout.publish(entry)
        for tap in list(self._taps):
            await tap(entry)
        if type == TERMINAL_EVENT:
            await self.seal()
        return entry

    async def seal(self) -> None:
        """End the log: the store refuses every later append and every live reader finishes."""
        self._sealed = True
        await self._store.seal(self._call)
        self._fanout.close()

    async def since(self, after: int = 0, limit: int = DEFAULT_LIMIT) -> list[Entry]:
        """One page of the durable log above the cursor, straight from the store."""
        return await self._store.since(self._call, after=after, limit=limit)

    async def whole(self) -> list[Entry]:
        """Every durable entry of this call, oldest first: what ring 4 judges at hang-up."""
        return await replay.whole(self._store, self._call)

    async def latest_seq(self) -> int:
        """The highest seq handed out, ephemerals counted."""
        return await self._store.latest_seq(self._call)

    def subscribe(self, filter: Filter = EVERYTHING) -> Subscription:
        """A live queue of what comes next. Nothing of the past: stream() is for that."""
        return self._fanout.subscribe(filter)

    def stream(self, after: int = 0, filter: Filter = EVERYTHING) -> AsyncIterator[Entry]:
        """The backlog above the cursor, log.caught_up, then live; a gap if it falls behind."""
        return replay.stream(self._store, self._fanout, self._call, after=after, filter=filter)


class AgentLog:
    """The agent's own log: registered, configured, an error outside a call. It never seals."""

    def __init__(
        self, store: Store, agent: str, *, fanout: Fanout | None = None, tap: Tap | None = None
    ) -> None:
        self._store = store
        self._agent = agent
        self._fanout = fanout or Fanout()
        self._taps = [tap] if tap is not None else []

    @property
    def agent(self) -> str:
        """The agent this log belongs to."""
        return self._agent

    async def append(
        self, type: str, data: JsonObject | None = None, ephemeral: bool | None = None
    ) -> Entry:
        """A line in the agent's own log. No masking: nothing personal happens outside a call."""
        forgettable = ephemeral_by_default(type) if ephemeral is None else ephemeral
        entry = await self._store.append(None, self._agent, type, data or {}, forgettable)
        self._fanout.publish(entry)
        for tap in list(self._taps):
            await tap(entry)
        return entry

    async def since(self, after: int = 0, limit: int = DEFAULT_LIMIT) -> list[Entry]:
        """One page of the agent's own log above the cursor."""
        return await self._store.agent_since(self._agent, after=after, limit=limit)

    async def calls(self) -> list[str]:
        """Every call this agent handled, oldest first."""
        return await self._store.list_calls(self._agent)

    def subscribe(self, filter: Filter = EVERYTHING) -> Subscription:
        """A live queue of the agent's own entries from now on."""
        return self._fanout.subscribe(filter)

    def stream(self, after: int = 0, filter: Filter = EVERYTHING) -> AsyncIterator[Entry]:
        """The backlog above the cursor, log.caught_up, then live. It never ends on its own."""
        return replay.agent_stream(
            self._store, self._fanout, self._agent, after=after, filter=filter
        )
