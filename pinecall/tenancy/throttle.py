"""How many requests an org sent a family of doors this minute, and whether one more is let in."""

import time
from collections.abc import Callable

# Requests a minute an org may send one family of doors in one world. Generous on purpose: the
# console polls its busiest screen every second and the rest every 3 to 60 s, so fifty tabs open
# at once stay under half of it. It is a wall against a runaway client, not a plan's limit.
REQUESTS_A_MINUTE = 6000

MINUTE_S = 60

# Names of minutes gone by are dropped once this many are kept.
SWEEP_AT = 4096


# In the process today; a later phase keeps the counts in Redis, behind this same method, so N
# gateways share one count per name and minute.
class Window:
    """Requests counted per name in the clock's current minute."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """A window nobody knocked at."""
        self.clock = clock
        self.counts: dict[str, tuple[int, int]] = {}

    async def counted(self, name: str, limit: int) -> float | None:
        """Count a request of the name: None within the limit, else the seconds until it resets."""
        now = self.clock()
        minute = int(now // MINUTE_S)
        if len(self.counts) >= SWEEP_AT:
            self.counts = {kept: count for kept, count in self.counts.items() if count[0] == minute}
        was, count = self.counts.get(name, (minute, 0))
        count = count + 1 if was == minute else 1
        self.counts[name] = (minute, count)
        if count <= limit:
            return None
        return (minute + 1) * MINUTE_S - now
