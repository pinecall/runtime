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


# How many names the table holds before it looks for ones that stopped knocking. The name is the
# KNOCKER's to choose — an email typed at the door — so a script trying a million addresses once
# each is a million entries nobody would ever prune by knocking again: the sweep is what bounds
# the table to the names of one window, whatever was tried before it.
SWEEP_AT = 1024


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
        sweep_at: int = SWEEP_AT,
    ) -> None:
        self._tries = tries
        self._window_s = window_s
        self._clock = clock
        self._sweep_at = sweep_at
        self._knocks: dict[str, deque[float]] = {}

    def allowed(self, name: str) -> bool:
        """Count this knock, and say whether it is within the window's allowance."""
        now = self._clock()
        if len(self._knocks) >= self._sweep_at:
            self._forget_the_quiet(now)
        knocks = self._knocks.setdefault(name, deque())
        self._forget_the_old(knocks, now)
        if len(knocks) >= self._tries:
            return False
        knocks.append(now)
        return True

    @property
    def names(self) -> int:
        """How many names the table holds right now."""
        return len(self._knocks)

    def _forget_the_old(self, knocks: deque[float], now: float) -> None:
        """Drop the knocks that fell out of the window."""
        while knocks and knocks[0] <= now - self._window_s:
            knocks.popleft()

    def _forget_the_quiet(self, now: float) -> None:
        """Drop every name whose knocks all fell out of the window."""
        for name in [
            name for name, knocks in self._knocks.items() if knocks[-1] <= now - self._window_s
        ]:
            del self._knocks[name]
