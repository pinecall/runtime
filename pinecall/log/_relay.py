"""The relay: what any gateway wrote, delivered to this process's live readers in seq order."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from pinecall.domain.errors import NotAvailable
from pinecall.log.store import Store, log_name
from pinecall.postgres.pool import box_task
from pinecall.process.signal import Listening, Signal
from pinecall.wire.frames import Entry

# Each log has a channel of its own, so a gateway hears only the logs something local reads;
# an org's feed in a world has one, and the box's floor one, followed without a sequencer: a
# feed spans calls and is live-only, each entry carrying its log and seq for a reader that
# needs a call's order.
LOG_PREFIX = "log:"
FEED_PREFIX = "feed:"
CHANNEL = "log:{name}"
FEED_CHANNEL = "feed:{org}:{env}"
BOX_CHANNEL = "box"

# A durable entry over this many bytes travels as its address, and the hearer reads it back.
STUB_OVER_BYTES = 64 * 1024

# How long an entry that arrived ahead of its turn waits for the ones before it, before the
# store is asked for them; and how often the store is asked for every followed log while the
# signal is down, when durable entries still arrive and ephemerals do not.
FILL_AFTER_S = 0.1
POLL_S = 1.0

# Both are told the channel: a log's (`log:{name}`), an org feed's, or the box's.
type Delivered = Callable[[str, Entry], None]
type Dropped = Callable[[str], None]


class Stored(BaseModel):
    """Where a big entry is: the hearer reads it back from the store."""

    log: str
    seq: int


class Travelled(BaseModel):
    """What a message on a channel carries: an entry whole, or the address of a big one."""

    model_config = ConfigDict(extra="forbid")

    # The relay that wrote it: its own delivery was local, so it skips it when heard back.
    sender: str
    entry: Entry | None = None
    stored: Stored | None = None


@dataclass
class Following:
    """One channel this process follows: its readers, the last seq delivered, what came early."""

    # The channel.
    name: str
    # A log is delivered in seq order; a feed as it comes.
    ordered: bool = True
    readers: int = 1
    last: int = 0
    held: dict[int, Entry] = field(default_factory=dict[int, Entry])
    # Set once the subscription is confirmed, or once the store is polled instead.
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    listening: Listening | None = None
    task: asyncio.Task[None] | None = None
    filling: asyncio.Task[None] | None = None
    closed: bool = False


# Stored before published, and every hearer resumes from the store: a message may be lost, or
# arrive out of turn, and what a reader is given is still whole and in seq order.
class Relay:
    """Follows the logs something local reads, and hands each entry on once, in its turn."""

    def __init__(
        self, store: Store, signal: Signal, delivered: Delivered, dropped: Dropped
    ) -> None:
        """Hand each entry to `delivered`; tell `dropped` when a log's readers must resume."""
        self.store = store
        self.signal = signal
        # Several relays may share one in-process signal: each skips only what it sent itself.
        self.id = uuid4().hex
        self._delivered = delivered
        self._dropped = dropped
        self._following: dict[str, Following] = {}

    @property
    def followed(self) -> int:
        """How many logs this process follows now."""
        return len(self._following)

    async def follow(self, name: str, *, ordered: bool = True) -> None:
        """Follow the channel for one more reader; returns once what is published next is heard."""
        following = self._following.get(name)
        if following is None:
            following = self._following[name] = Following(name, ordered=ordered)
            following.task = box_task(self._run(following))
        else:
            following.readers += 1
        await following.ready.wait()

    def unfollow(self, name: str) -> None:
        """One reader left the log; the last one leaving lets the channel go."""
        following = self._following.get(name)
        if following is None:
            return
        following.readers -= 1
        if following.readers <= 0:
            self._let_go(following)

    def local(self, channel: str, entry: Entry) -> None:
        """An entry this process wrote: told to every gateway, and delivered here in its turn."""
        self.signal.publish(channel, encoded(entry, self.id))
        following = self._following.get(channel)
        if following is None or not following.ordered or not following.ready.is_set():
            self._delivered(channel, entry)
        else:
            self._sequenced(following, entry)

    async def close(self) -> None:
        """Let every log go and wait for their tasks."""
        followings = list(self._following.values())
        for following in followings:
            self._let_go(following)
        tasks = [following.task for following in followings if following.task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, following: Following) -> None:
        try:
            listening = await self.signal.subscribe(following.name)
        except NotAvailable:
            listening = None
        if following.ordered:
            following.last = await self.store.latest_seq(following.name.removeprefix(LOG_PREFIX))
        following.listening = listening
        following.ready.set()
        if following.closed:
            if listening is not None:
                listening.close()
            return
        if listening is None:
            await self._polled(following)
            return
        async for data in listening:
            await self._heard(following, data)
        self._over(following)

    # The signal is down: a log's durable entries are read off the store each second (a feed
    # spans calls and cannot be, so its readers hear this process alone), until it is back, when
    # the readers are told to resume so the channel is followed again.
    async def _polled(self, following: Following) -> None:
        while not following.closed:
            await asyncio.sleep(POLL_S)
            if following.closed:
                return
            if following.ordered:
                for entry in await self.store.since(
                    following.name.removeprefix(LOG_PREFIX), after=following.last
                ):
                    if entry.seq > following.last:
                        self._handed(following, entry)
            if self.signal.up:
                self._over(following)
                return

    async def _heard(self, following: Following, data: bytes) -> None:
        travelled = Travelled.model_validate_json(data)
        if travelled.sender == self.id:
            return
        if travelled.entry is not None:
            self._taken(following, travelled.entry)
        elif travelled.stored is not None:
            seq = travelled.stored.seq
            found = await self.store.since(travelled.stored.log, after=seq - 1, limit=1)
            if found and found[0].seq == seq:
                self._taken(following, found[0])

    def _taken(self, following: Following, entry: Entry) -> None:
        if following.ordered:
            self._sequenced(following, entry)
        elif not following.closed:
            self._delivered(following.name, entry)

    def _sequenced(self, following: Following, entry: Entry) -> None:
        if following.closed or entry.seq <= following.last:
            return
        if entry.seq == following.last + 1:
            self._handed(following, entry)
            while (queued := following.held.pop(following.last + 1, None)) is not None:
                self._handed(following, queued)
            return
        following.held[entry.seq] = entry
        if following.filling is None:
            following.filling = box_task(self._filled(following))

    # What is missing before the held ones is read from the store: by the head's lock every
    # durable entry below a seq given out is committed, so a seq not found is an ephemeral, lost.
    async def _filled(self, following: Following) -> None:
        await asyncio.sleep(FILL_AFTER_S)
        try:
            if following.closed or not following.held:
                return
            highest = max(following.held)
            wanted = highest - following.last
            name = following.name.removeprefix(LOG_PREFIX)
            for entry in await self.store.since(name, after=following.last, limit=wanted):
                if following.last < entry.seq <= highest:
                    following.held.setdefault(entry.seq, entry)
            for seq in sorted(seq for seq in following.held if seq <= highest):
                entry = following.held.pop(seq)
                if seq > following.last:
                    self._handed(following, entry)
        finally:
            following.filling = None
            if following.held and not following.closed:
                following.filling = box_task(self._filled(following))

    # The channel was lost: this following is over, so a reader that comes back follows a new one
    # (subscribed again) rather than this one, which nobody listens on; its readers are dropped.
    def _over(self, following: Following) -> None:
        if following.closed:
            return
        following.closed = True
        if self._following.get(following.name) is following:
            del self._following[following.name]
        self._dropped(following.name)

    def _handed(self, following: Following, entry: Entry) -> None:
        following.last = entry.seq
        self._delivered(following.name, entry)

    def _let_go(self, following: Following) -> None:
        following.closed = True
        self._following.pop(following.name, None)
        if following.listening is not None:
            following.listening.close()
        if following.filling is not None:
            following.filling.cancel()
        if following.task is not None and following.task is not asyncio.current_task():
            following.task.cancel()


# A big entry's address names its own log, which a feed's channel does not.
def encoded(entry: Entry, sender: str) -> bytes:
    """The message an entry travels as: itself, or its address when it is big and durable."""
    whole = f'{{"sender":{json.dumps(sender)},"entry":{entry.written_json()}}}'
    if len(whole) > STUB_OVER_BYTES and not entry.ephemeral:
        log = log_name(entry.call, entry.agent)
        stub = {"sender": sender, "stored": {"log": log, "seq": entry.seq}}
        return json.dumps(stub, separators=(",", ":")).encode()
    return whole.encode()
