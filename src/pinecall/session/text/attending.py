"""call.attention on a written conversation: the thread waits for a person to take it."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pinecall.session.attention import ALREADY_WAITING, NOBODY_TOOK_IT
from pinecall_protocol import ProtocolError
from pinecall_protocol.commands import CallAttention
from pinecall_protocol.defs import Supervisor
from pinecall_protocol.events import AttentionAnswered, AttentionRequested

if TYPE_CHECKING:
    from pinecall.session.text.session import TextSession


# A thread has no audio to hold, so waiting is the model falling quiet: what the contact writes
# while the ask is open is logged and left unanswered, exactly as it is under a takeover, and the
# person who takes the thread reads it. The ask ends the same two ways a spoken one does.
class Attending:
    """The agent's ask for a person on a thread: the entry, the quiet, and what ends the wait."""

    def __init__(self, session: TextSession) -> None:
        self._session = session
        self._waiting: asyncio.Task[None] | None = None
        self.open = False

    async def asked(self, wanted: CallAttention) -> None:
        """attention.requested: the thread waits, and the model answers nothing while it does."""
        if self.open:
            raise ProtocolError(ALREADY_WAITING)
        self.open = True
        await self._session.emit(
            "attention.requested",
            AttentionRequested(reason=wanted.reason, wait_s=wanted.wait_s),
        )
        self._waiting = asyncio.ensure_future(self._until(wanted.wait_s))

    async def taken_by(self, by: Supervisor) -> None:
        """A supervisor took the thread: the ask is answered, and they are writing now."""
        if not self.open:
            return
        self._stop_counting()
        self.open = False
        await self._session.emit("attention.answered", AttentionAnswered(ok=True, by=by))

    def close(self) -> None:
        """The call is over: nothing is waiting for anybody any more."""
        self._stop_counting()
        self.open = False

    async def _until(self, wait_s: float) -> None:
        """The wait: when it runs out with nobody there, the model has the thread back."""
        await asyncio.sleep(wait_s)
        self.open = False
        await self._session.emit(
            "attention.answered",
            AttentionAnswered(ok=False, by=None, error=NOBODY_TOOK_IT.format(wait_s=wait_s)),
        )

    def _stop_counting(self) -> None:
        if self._waiting is not None and not self._waiting.done():
            self._waiting.cancel()
        self._waiting = None
