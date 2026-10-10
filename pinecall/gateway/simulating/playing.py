"""The simulated calls this process is playing: a task each, by call, stopped with the process."""

import asyncio
import logging
from collections.abc import Coroutine

logger = logging.getLogger(__name__)

BROKE = "simulated call %s broke"


class Simulations:
    """What plays each simulated call after its door has answered, and stops them all at the end."""

    def __init__(self) -> None:
        """Nothing playing."""
        self.playing: dict[str, asyncio.Task[None]] = {}

    # The request's context goes with the task, so the call is played as the org that asked for it.
    def play(self, call: str, played: Coroutine[object, object, object]) -> None:
        """Play the call on its own task, kept until it ends; a break is logged, never lost."""

        async def kept() -> None:
            await played

        task = asyncio.create_task(kept())
        self.playing[call] = task
        task.add_done_callback(lambda done: self._ended(call, done))

    def _ended(self, call: str, done: asyncio.Task[None]) -> None:
        self.playing.pop(call, None)
        if not done.cancelled() and done.exception() is not None:
            logger.error(BROKE, call, exc_info=done.exception())

    async def stopped(self) -> None:
        """Stop every call still playing: each one's caller hangs up as its task is cancelled."""
        playing = list(self.playing.values())
        for task in playing:
            task.cancel()
        await asyncio.gather(*playing, return_exceptions=True)
