"""The relay: what any gateway wrote, delivered to this process's live readers in seq order."""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from pinecall.domain.errors import NotAvailable
from pinecall.log.store import Store
from pinecall.process.signal import Listening, Signal
from pinecall.wire.frames import Entry

logger = logging.getLogger(__name__)

# Each log has a channel of its own: a gateway hears only the logs something local reads.
CHANNEL = "log:{name}"

# A durable entry over this many bytes travels as its address, and the hearer reads it back.
STUB_OVER_BYTES = 64 * 1024

# How long an entry that arrived ahead of its turn waits for the ones before it, before the
# store is asked for them; and how often the store is asked for every followed log while the
# signal is down, when durable entries still arrive and ephemerals do not.
FILL_AFTER_S = 0.1
POLL_S = 1.0

type Delivered = Callable[[str, Entry], None]


type Dropped = Callable[[str], None]


class Stored(BaseModel):
    """Where a big entry is: the hearer reads it back from the store."""

    log: str
    seq: int


class Travelled(BaseModel):
    """What a message on a log's channel carries: an entry whole, or the address of a big one."""

    model_config = ConfigDict(extra="forbid")

    entry: Entry | None = None
    stored: Stored | None = None


@dataclass
class Following:
    """One log this process follows: its readers, the last seq delivered, what arrived early."""

    name: str
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
        self._delivered = delivered
        self._dropped = dropped
        self._following: dict[str, Following] = {}

    @property
    def followed(self) -> int:
        """How many logs this process follows now."""
        return len(self._following)

    async def follow(self, name: str) -> None:
        """Follow the log for one more reader; returns once what is published next is heard."""
        following = self._following.get(name)
        if following is None:
            following = self._following[name] = Following(name)
            following.task = asyncio.create_task(self._run(following))
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

    def local(self, name: str, entry: Entry) -> None:
        """An entry this process wrote: told to every gateway, and delivered here in its turn."""
        self.signal.publish(CHANNEL.format(name=name), encoded(name, entry))
        following = self._following.get(name)
        if following is None or not following.ready.is_set():
            self._delivered(name, entry)
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
            listening = await self.signal.subscribe(CHANNEL.format(name=following.name))
        except NotAvailable:
            listening = None
        following.last = await self.store.latest_seq(following.name)
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
        if not following.closed:
            self._dropped(following.name)

    # The signal is down: durable entries are read off the store each second, until it is back,
    # when the readers are told to resume so the channel is followed again.
    async def _polled(self, following: Following) -> None:
        while not following.closed:
            await asyncio.sleep(POLL_S)
            if following.closed:
                return
            for entry in await self.store.since(following.name, after=following.last):
                if entry.seq > following.last:
                    self._handed(following, entry)
            if self.signal.up:
                self._dropped(following.name)
                return

    async def _heard(self, following: Following, data: bytes) -> None:
        travelled = Travelled.model_validate_json(data)
        if travelled.entry is not None:
            self._sequenced(following, travelled.entry)
        elif travelled.stored is not None:
            seq = travelled.stored.seq
            found = await self.store.since(following.name, after=seq - 1, limit=1)
            if found and found[0].seq == seq:
                self._sequenced(following, found[0])

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
            following.filling = asyncio.create_task(self._filled(following))

    # What is missing before the held ones is read from the store: by the head's lock every
    # durable entry below a seq given out is committed, so a seq not found is an ephemeral, lost.
    async def _filled(self, following: Following) -> None:
        await asyncio.sleep(FILL_AFTER_S)
        try:
            if following.closed or not following.held:
                return
            highest = max(following.held)
            wanted = highest - following.last
            for entry in await self.store.since(following.name, after=following.last, limit=wanted):
                if following.last < entry.seq <= highest:
                    following.held.setdefault(entry.seq, entry)
            for seq in sorted(seq for seq in following.held if seq <= highest):
                entry = following.held.pop(seq)
                if seq > following.last:
                    self._handed(following, entry)
        finally:
            following.filling = None
            if following.held and not following.closed:
                following.filling = asyncio.create_task(self._filled(following))

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


def encoded(name: str, entry: Entry) -> bytes:
    """The message an entry travels as: itself, or its address when it is big and durable."""
    whole = json.dumps({"entry": entry.written()}, separators=(",", ":")).encode()
    if len(whole) > STUB_OVER_BYTES and not entry.ephemeral:
        stub = {"stored": {"log": name, "seq": entry.seq}}
        return json.dumps(stub, separators=(",", ":")).encode()
    return whole
