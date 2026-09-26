"""What a spoken call writes after call.ended: memory, then the verdict; bounded, said if not."""

from __future__ import annotations

import asyncio
import logging

from pinecall.session.remember_step import Rememberer, not_remembered, remembered_within
from pinecall.session.score_step import Scorer, nobody_judged
from pinecall.session.voice.log_writer import Writing
from pinecall.session.voice.platform import Platform
from pinecall.settings import Budgets
from pinecall.types import AgentConfig, CallContext
from pinecall_protocol.codec import decode_entry
from pinecall_protocol.events import CallScore

logger = logging.getLogger(__name__)

# The seal's budget ran out before the log reached the platform, or came back from it.
THE_LOG_NEVER_ARRIVED = "the call's log never reached the platform within the seal's budget"


# The two things a hang-up asks of the platform once the call is over, each inside the seal's
# budget (Budgets.seal_s): a platform away at that moment costs the memory and the verdict, said
# as such on the log, and never the seal itself — the reaper finishes what this could not.
class Sealing:
    """Memory at hang-up and the verdict on the call, both asked of the platform, both bounded."""

    def __init__(
        self,
        context: CallContext,
        config: AgentConfig,
        platform: Platform,
        writing: Writing,
        *,
        score: Scorer,
        rememberer: Rememberer,
        budgets: Budgets,
    ) -> None:
        self._context = context
        self._config = config
        self._platform = platform
        self._writing = writing
        self._score = score
        self._rememberer = rememberer
        self._budgets = budgets

    # After call.ended and before call.summary, flushed first: the platform reads the turns back
    # off the log this process writes to, and the last one has to be there. An agent that declared
    # no memory has nothing to remember and asks nobody. See docs/decisions/memory.md.
    async def remembered(self) -> None:
        """What this call taught about the contact, written by the platform; a miss is an entry."""
        if self._config.memory is None:
            return
        try:
            await self._writing.flushed(self._budgets.seal_s)
        except TimeoutError:
            await self._writing.emit("error", not_remembered(THE_LOG_NEVER_ARRIVED))
            return
        failed = await remembered_within(
            self._rememberer, self._context.call, self._budgets.remember_s
        )
        if failed is not None:
            await self._writing.emit("error", failed)

    # A verdict is read by the seqs it names, and this process never learns one: the platform
    # numbers the log. So the call is read back through the same door it was written to, which is
    # the whole of what a spoken call may know about the platform. See docs/decisions/scoring.md.
    # A platform that cannot be read within the budget costs the verdict, said as such, and
    # nothing else: the reaper judges what the worker could not.
    async def verdict(self) -> CallScore:
        """This call's own log back from the platform, and what the judge makes of it."""
        try:
            async with asyncio.timeout(self._budgets.seal_s):
                await self._writing.flushed()
                entries = [
                    decode_entry(raw) async for raw in self._platform.since(self._context.call, 0)
                ]
        except Exception as away:
            logger.warning("call %s: not judged, %s", self._context.call, away)
            return nobody_judged(THE_LOG_NEVER_ARRIVED)
        return await self._score(entries, self._config)
