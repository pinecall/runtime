"""How many requests an org sent a family of doors this minute, and whether one more is let in."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import TypeAdapter

from pinecall.process.shared import Shared
from pinecall.process.signal import LocalSignal, Signal

# Requests a minute an org may send one family of doors in one world. Generous on purpose: the
# console polls its busiest screen every second and the rest every 3 to 60 s, so fifty tabs open
# at once stay under half of it. It is a wall against a runaway client, not a plan's limit.
REQUESTS_A_MINUTE = 6000

MINUTE_S = 60

# Names of minutes gone by are dropped once this many are kept.
SWEEP_AT = 4096

# Each gateway says its busy names every second; a name under this many requests here is not
# said, so the wall is late by at most this many a gateway, and the share stays small.
PACED_CHANNEL = "paced"
PACED_EVERY_S = 1.0
SAID_FROM = 100


@dataclass(frozen=True)
class Paced:
    """One gateway's busy names this minute, and how many requests each sent it."""

    minute: int = 0
    counts: dict[str, int] = field(default_factory=dict[str, int])


# Counted here, and summed with what the other gateways said this minute: a request pays no round
# trip, and a name spread over N gateways meets the wall within a second of reaching it.
class Window:
    """Requests counted per name in the clock's current minute, across the box's gateways."""

    def __init__(
        self, clock: Callable[[], float] = time.time, signal: Signal | None = None
    ) -> None:
        """A window nobody knocked at."""
        self.clock = clock
        self.counts: dict[str, tuple[int, int]] = {}
        self.shared = Shared(signal or LocalSignal(), PACED_CHANNEL, _PACED, Paced(), lambda: None)
        self.shared.every_s = PACED_EVERY_S

    async def start(self) -> None:
        """Hear the other gateways' busy names, and say this one's every second."""
        await self.shared.start()

    async def close(self) -> None:
        """Stop hearing and saying."""
        await self.shared.close()

    async def counted(self, name: str, limit: int) -> float | None:
        """Count a request of the name: None within the limit, else the seconds until it resets."""
        now = self.clock()
        minute = int(now // MINUTE_S)
        if len(self.counts) >= SWEEP_AT:
            self.counts = {kept: count for kept, count in self.counts.items() if count[0] == minute}
        was, count = self.counts.get(name, (minute, 0))
        count = count + 1 if was == minute else 1
        self.counts[name] = (minute, count)
        if count % SAID_FROM == 0:
            self._keep_saying(minute)
        elsewhere = sum(
            heard.share.counts.get(name, 0)
            for heard in self.shared.theirs.values()
            if heard.share.minute == minute
        )
        if count + elsewhere <= limit:
            return None
        return (minute + 1) * MINUTE_S - now

    def _keep_saying(self, minute: int) -> None:
        busy = {
            name: count
            for name, (was, count) in self.counts.items()
            if was == minute and count >= SAID_FROM
        }
        self.shared.mine = Paced(minute, busy)


_PACED: TypeAdapter[Paced] = TypeAdapter(Paced)
