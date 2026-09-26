"""One call's entries on their way to the platform, in the order the session produced them."""

from __future__ import annotations

import asyncio
import logging

from livekit.agents.utils.aio import cancel_and_wait

from pinecall.session.voice.platform import Platform
from pinecall_protocol import WireModel, encode

logger = logging.getLogger(__name__)


# livekit emits most of what a log wants from synchronous callbacks — a metric off a component, an
# item added to the conversation — and the platform is an await away. One queue drained by one task
# is what keeps those two facts from becoming a log whose entries arrive in the wrong order.
class Writing:
    """The bridge's one hand on the log: every entry queued here, sent in that order, once."""

    def __init__(self, platform: Platform, call: str) -> None:
        self._platform = platform
        self._call = call
        self._queued: asyncio.Queue[tuple[str, WireModel, bool | None]] = asyncio.Queue()
        self._draining: asyncio.Task[None] | None = None
        self.refused: list[str] = []

    def open(self) -> None:
        """Start draining. Called once, when the session is about to start."""
        if self._draining is None:
            self._draining = asyncio.ensure_future(self._drain())

    async def emit(self, type: str, event: WireModel, ephemeral: bool | None = None) -> None:
        """Write one entry of this call. It is sent behind everything already queued."""
        await self._queued.put((type, event, ephemeral))

    def later(self, type: str, event: WireModel, ephemeral: bool | None = None) -> None:
        """Write one entry from a synchronous callback: the queue is what makes this safe."""
        self._queued.put_nowait((type, event, ephemeral))

    async def flushed(self, within_s: float | None = None) -> None:
        """Every entry queued so far has reached the platform: the last step before the seal.
        TimeoutError past `within_s`: the platform is away, and the entries are still queued."""
        await asyncio.wait_for(self._queued.join(), within_s)

    async def close(self, within_s: float | None = None) -> None:
        """Send what is left, then stop draining: nothing may be queued after this. Past
        `within_s` what is still queued is said, counted, and let go: a seal the gateway's own
        reaper finishes beats a job the worker has to kill."""
        try:
            await self.flushed(within_s)
        except TimeoutError:
            logger.warning(
                "call %s: %d entries never reached the platform within %ss",
                self._call,
                self._queued.qsize(),
                within_s,
            )
        if self._draining is not None:
            await cancel_and_wait(self._draining)
            self._draining = None
        if self.refused:
            logger.warning(
                "call %s: the platform refused %d entries (%s)",
                self._call,
                len(self.refused),
                ", ".join(sorted(set(self.refused))),
            )

    # A platform that refuses one entry must not end the call: the caller is on the line and the
    # rest of the log is still worth writing. A gateway that is only away — restarting — is waited
    # out by the worker's client (worker/retries.py), so what lands here is a real no, a 4xx: it
    # is remembered and said once, at the close: how much of the call's log is missing.
    async def _drain(self) -> None:
        """One entry at a time, in order, for as long as the call lasts."""
        while True:
            type, event, ephemeral = await self._queued.get()
            try:
                await self._platform.append(self._call, type, encode(event), ephemeral)
            except asyncio.CancelledError:
                # The close gave up on the platform: this entry never landed, and is counted.
                self.refused.append(type)
                raise
            except Exception:
                self.refused.append(type)
            finally:
                self._queued.task_done()
