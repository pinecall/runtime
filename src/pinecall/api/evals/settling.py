"""Waiting for the connected app: it answers a call's entries with its own, and then stops."""

from __future__ import annotations

import asyncio
import time

from pinecall.log.entry import Entry
from pinecall.session.text.session import TextSession

# An app reacts to call.started by rendering, and to event.received by running the tenant's own
# handler: prompt.set, state.set, tools.set come back down its socket a moment later. Nothing on
# the wire says "that was all of it", so the runner waits for the log to go quiet instead. Short
# enough that a golden of ten turns does not spend two seconds waiting, long enough that a render
# and a re-render are not read as two separate silences.
QUIET_S = 0.15

# An app that never answers must not hold the run: past this, the conversation goes on without
# whatever it was going to say. A render is milliseconds; a second is already a broken app.
AT_MOST_S = 2.0


class Settling:
    """A watcher on one call that knows when its log last moved, so the runner can wait it out."""

    def __init__(self, session: TextSession) -> None:
        self._last = time.monotonic()
        session.watch(self._heard)

    async def settled(self, quiet_s: float = QUIET_S, at_most_s: float = AT_MOST_S) -> None:
        """Return when nothing has been written for `quiet_s`, and always within `at_most_s`."""
        deadline = time.monotonic() + at_most_s
        while True:
            quiet_for = time.monotonic() - self._last
            if quiet_for >= quiet_s or time.monotonic() >= deadline:
                return
            await asyncio.sleep(quiet_s - quiet_for)

    # Every entry of the call passes here, the runner's own included: what is being waited for is
    # silence on the log, and an entry the runner wrote is as good a sign of life as the app's.
    async def _heard(self, _entry: Entry) -> None:
        """One more entry on this call, whoever wrote it."""
        self._last = time.monotonic()
