"""Every live reader of one log. Bounded queues: a slow reader is dropped, an append never waits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from pinecall.log.entry import Entry
from pinecall.log.filters import EVERYTHING, Filter

# How far behind a reader may fall before the log stops carrying it. 256 entries is about a minute
# of a busy call: long enough for a browser to render a burst, short enough that a dead socket
# cannot pin a call's worth of memory. Past it the reader is dropped and told, never waited for.
QUEUE_DEPTH = 256


class Subscription:
    """One reader's queue. Iterate it for entries; it ends when the log closes or it fell behind."""

    def __init__(
        self,
        filter: Filter = EVERYTHING,
        depth: int = QUEUE_DEPTH,
        on_close: Callable[[Subscription], None] = lambda _: None,
    ) -> None:
        self.filter = filter
        self._depth = depth
        self._on_close = on_close
        # One slot past the depth, kept for the end sentinel: closing a full queue must not block
        # either, and a reader that never learns the stream ended is a reader that hangs.
        self._queue: asyncio.Queue[Entry | None] = asyncio.Queue(maxsize=depth + 1)
        self._dropped = False
        self._ended = False

    @property
    def dropped(self) -> bool:
        """True when this reader fell behind: what it has read is whole, what follows it missed."""
        return self._dropped

    def offer(self, entry: Entry) -> bool:
        """The fanout's door. False means this reader is gone and the fanout should let it go."""
        if self._ended:
            return False
        if not self.filter.passes(entry):
            return True
        if self._queue.qsize() >= self._depth:
            self._dropped = True
            self._end()
            return False
        self._queue.put_nowait(entry)
        return True

    def close(self) -> None:
        """Stop the iteration and leave the fanout. What is already queued is still delivered."""
        self._end()
        self._on_close(self)

    def _end(self) -> None:
        """The sentinel that ends the iteration, put once, into the slot kept for it."""
        if not self._ended:
            self._ended = True
            self._queue.put_nowait(None)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Entry:
        """Entries in the order the log wrote them, until the sentinel says there are no more."""
        entry = await self._queue.get()
        if entry is None:
            raise StopAsyncIteration
        return entry


class Fanout:
    """The live readers of one log. publish() never awaits, so an append never waits on a reader."""

    def __init__(self, depth: int = QUEUE_DEPTH) -> None:
        self._depth = depth
        self._subscriptions: set[Subscription] = set()
        self._closed = False

    @property
    def readers(self) -> int:
        """How many readers are being carried right now. A test reads it; nothing else should."""
        return len(self._subscriptions)

    def subscribe(self, filter: Filter = EVERYTHING) -> Subscription:
        """A queue of this log's entries from now on. A closed log hands back an ended one."""
        subscription = Subscription(filter, self._depth, self._subscriptions.discard)
        if self._closed:
            subscription.close()
            return subscription
        self._subscriptions.add(subscription)
        return subscription

    def publish(self, entry: Entry) -> None:
        """Offer the entry to every reader. Whoever cannot take it is dropped, here and now."""
        for subscription in tuple(self._subscriptions):
            if not subscription.offer(entry):
                self._subscriptions.discard(subscription)

    def close(self) -> None:
        """The log ended: every reader's iteration finishes once it has read what it was given."""
        self._closed = True
        for subscription in tuple(self._subscriptions):
            subscription.close()
        self._subscriptions.clear()
