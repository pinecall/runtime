"""call.attention on a spoken call: the caller waits on hold until somebody takes the line."""

from __future__ import annotations

import asyncio
import logging

from pinecall.session.attention import ALREADY_WAITING, NOBODY_TOOK_IT
from pinecall.session.voice.line import Line
from pinecall.session.voice.writing import Writing
from pinecall_protocol import ProtocolError
from pinecall_protocol.commands import CallAttention
from pinecall_protocol.defs import Supervisor
from pinecall_protocol.events import AttentionAnswered, AttentionRequested

logger = logging.getLogger(__name__)


# One per call, built with the session, because an ask and the takeover that answers it are two
# frames a minute apart. The wait itself is a task: the caller is on hold and something has to
# give the line back if nobody comes, and nothing else in the session is counting.
class Attending:
    """The agent's ask for a person: the entry, the hold, and whichever of the two ends it."""

    def __init__(self, line: Line, writing: Writing) -> None:
        self._line = line
        self._writing = writing
        self._waiting: asyncio.Task[None] | None = None
        self.open = False

    async def asked(self, wanted: CallAttention) -> None:
        """attention.requested, and the caller waits: this is what a supervisor is notified by."""
        if self.open:
            raise ProtocolError(ALREADY_WAITING)
        self.open = True
        await self._writing.emit(
            "attention.requested",
            AttentionRequested(reason=wanted.reason, wait_s=wanted.wait_s),
        )
        await self._line.hold()
        self._waiting = asyncio.ensure_future(self._until(wanted.wait_s))

    # Whether or not an ask was open: a caller on a plain hold whose line a supervisor takes is
    # with a person now, and the melody has to stop for them exactly the same.
    async def taken_by(self, by: Supervisor) -> None:
        """A supervisor took the line: the ask, if one was open, is answered; any hold ends."""
        if self.open:
            self._stop_counting()
            self.open = False
            await self._writing.emit("attention.answered", AttentionAnswered(ok=True, by=by))
        # The takeover has the line deaf and mute already, and is about to say so itself.
        await self._line.unhold(speaks_again=False)

    def close(self) -> None:
        """The call is over: nothing is waiting for anybody any more."""
        self._stop_counting()
        self.open = False

    async def _until(self, wait_s: float) -> None:
        """The wait: when it runs out with nobody there, the agent has the caller back."""
        await asyncio.sleep(wait_s)
        self.open = False
        await self._writing.emit(
            "attention.answered",
            AttentionAnswered(ok=False, by=None, error=NOBODY_TOOK_IT.format(wait_s=wait_s)),
        )
        await self._line.unhold()

    def _stop_counting(self) -> None:
        if self._waiting is not None and not self._waiting.done():
            self._waiting.cancel()
        self._waiting = None
