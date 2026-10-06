"""State each gateway holds a share of, told to the others on the signal and merged by each."""

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from pinecall.domain.errors import NotAvailable
from pinecall.postgres.pool import box_task
from pinecall.process.signal import Signal

# Each process says its whole share this often, and forgets another's share once it has been
# silent this long: a gateway that died takes its share with it within half a minute.
TOLD_EVERY_S = 10.0
SILENT_AT_MOST_S = 30.0

# A share said changes nothing past the next beat: a lost message is repaired by it.
RETRY_S = 1.0

# A key set to nobody is kept this long, so an older setting said by another process does not
# come back while that process still says it.
FORGOTTEN_AFTER_S = 60.0


class Said(BaseModel):
    """One message on a share's channel: which process said it, and its share whole."""

    sender: str
    share: object


@dataclass(frozen=True)
class Assigned:
    """A key of a world as one process last set it: to a holder, or to nobody, and when."""

    env: str
    key: str
    holder: str | None
    at: float


@dataclass
class Heard[T]:
    """Another process's share, and when it last said it."""

    share: T
    at: float


# Lossy by design, as the signal is: every share is said whole, again and again, so a message lost
# or a Redis that restarted costs at most one beat. What a share holds must be small (sockets,
# lines, phones): it travels whole every TOLD_EVERY_S.
class Shared[T]:
    """One share of this process, the others' as last heard, and a callback when either moves."""

    def __init__(
        self,
        signal: Signal,
        channel: str,
        adapter: TypeAdapter[T],
        empty: T,
        changed: Callable[[], None],
    ) -> None:
        """Nothing heard yet; `changed` is called whenever the merged view must be rebuilt."""
        self.signal = signal
        # How often the share is said whole: TOLD_EVERY_S unless its owner says sooner.
        self.every_s = TOLD_EVERY_S
        # Set by an owner whose share changes too often to put: read as it is said.
        self.gathered: Callable[[], T] | None = None
        self.channel = channel
        self.id = uuid4().hex
        self.mine = empty
        self.theirs: dict[str, Heard[T]] = {}
        self._adapter = adapter
        self._changed = changed
        self._tasks: list[asyncio.Task[None]] = []

    def put(self, share: T) -> None:
        """This process's share changed: keep it, and say it at once."""
        self.mine = share
        self._say()

    async def start(self) -> None:
        """Listen to the others and say this share every beat."""
        self._tasks = [box_task(self._listened()), box_task(self._beating())]

    async def close(self) -> None:
        """Stop listening and saying."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _say(self) -> None:
        mine = self.mine if self.gathered is None else self.gathered()
        message = Said(sender=self.id, share=self._adapter.dump_python(mine, mode="json"))
        self.signal.publish(self.channel, message.model_dump_json().encode())

    async def _beating(self) -> None:
        while True:
            self._say()
            await asyncio.sleep(self.every_s)
            now = time.monotonic()
            silent = [gw for gw, heard in self.theirs.items() if now - heard.at > SILENT_AT_MOST_S]
            for gateway in silent:
                del self.theirs[gateway]
            if silent:
                self._changed()

    async def _listened(self) -> None:
        while True:
            try:
                listening = await self.signal.subscribe(self.channel)
            except NotAvailable:
                await asyncio.sleep(RETRY_S)
                continue
            # Said again once subscribed: a process that just started is told everyone's share.
            self._say()
            try:
                async for data in listening:
                    self._heard(data)
            finally:
                listening.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.signal.connected(), RETRY_S)

    def _heard(self, data: bytes) -> None:
        message = Said.model_validate_json(data)
        if message.sender == self.id:
            return
        new = message.sender not in self.theirs
        share = self._adapter.validate_python(message.share)
        self.theirs[message.sender] = Heard(share, time.monotonic())
        self._changed()
        if new:
            self._say()


# The newest setting of each key stands, whichever process said it. A setting of ours another
# overruled is dropped, and one to nobody is kept only a while: what is said every beat stays the
# size of what is set.
def newest(
    mine: dict[tuple[str, str], Assigned], theirs: Iterable[Assigned]
) -> dict[tuple[str, str], str]:
    """Each key's holder by its newest setting, ours and theirs; ours pruned of what is spent."""
    latest = dict(mine)
    for assigned in theirs:
        key = (assigned.env, assigned.key)
        if key not in latest or latest[key].at < assigned.at:
            latest[key] = assigned
    now = time.time()
    for key, kept in list(mine.items()):
        if latest[key] is not kept or (kept.holder is None and now - kept.at > FORGOTTEN_AFTER_S):
            del mine[key]
    return {key: setting.holder for key, setting in latest.items() if setting.holder is not None}
