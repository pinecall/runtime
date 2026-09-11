"""The rate limit at a door a password knocks on: so many tries per name per minute, then 429."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

# Five wrong passwords a minute is a person mistyping; fifty is a script. The window is short on
# purpose — a locked-out person waits a minute, not an afternoon — and it counts EVERY try, right
# or wrong, so a right password on the sixth try in a minute still waits: a limiter that only
# counted misses would tell a script which guess was the hit.
TRIES_PER_WINDOW = 5
WINDOW_S = 60.0


# This process's memory: a limit is a fact about who is knocking right now, and a restart that
# forgets a burst forgets nothing that matters. Keyed by whatever the door names — the email, the
# client's address — so one name being guessed does not lock the whole door.
class Throttle:
    """How many times each name has knocked lately, and whether one more is allowed."""

    def __init__(
        self,
        tries: int = TRIES_PER_WINDOW,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._tries = tries
        self._window_s = window_s
        self._clock = clock
        self._knocks: dict[str, deque[float]] = {}

    def allowed(self, name: str) -> bool:
        """Count this knock, and say whether it is within the window's allowance."""
        now = self._clock()
        knocks = self._knocks.setdefault(name, deque())
        while knocks and knocks[0] <= now - self._window_s:
            knocks.popleft()
        if len(knocks) >= self._tries:
            return False
        knocks.append(now)
        return True
