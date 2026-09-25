"""A second leg dialled into the call's room: the agent falls silent and the call goes with them."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from livekit import rtc
from livekit.agents.voice import AgentSession

from pinecall.session.voice.room.facts import LEFT
from pinecall.session.voice.room.holding import Holding
from pinecall_protocol.defs import EndReason

# The type only, and never at import time: commands.py builds this class when a warm transfer
# takes, so naming its module here for real would close the circle.
if TYPE_CHECKING:
    from pinecall.session.voice.commands import Ending

logger = logging.getLogger(__name__)

# How a call that ended after a warm transfer reads, whoever put the phone down first: the caller
# was handed to a person and the conversation was theirs from then on.
TRANSFERRED: EndReason = "transferred"


# A warm transfer does not move the caller anywhere: the person is dialled INTO the room and the
# three of them are in it for a moment. So the agent goes mute and deaf — what it cannot hear it
# cannot later claim to remember, exactly as a supervisor's takeover — and the call ends when
# either of the two humans hangs up. The caller leaving closes the session on its own; the person
# leaving does not, and a caller left alone with an agent that will not speak is the one ending
# this has to write itself.
class Bridged:
    """The far end took a warm transfer: what the agent does for the rest of the call, and when."""

    def __init__(self, live: AgentSession[None], holding: Holding, ending: Ending) -> None:
        self._live = live
        self._holding = holding
        self._ending = ending
        self._leg: str | None = None
        self._ending_it: asyncio.Task[None] | None = None

    async def took(self, identity: str) -> None:
        """The person answered: the agent stops speaking and hearing, and the call is theirs."""
        self._leg = identity
        await self._cut_the_sentence()
        self._live.output.set_audio_enabled(False)
        self._live.input.set_audio_enabled(False)
        # Whatever closes this session from here, the log says the caller was transferred.
        self._ending.transferred()
        self._holding.room.on(LEFT, self._somebody_left)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`

    def _somebody_left(self, participant: rtc.RemoteParticipant) -> None:
        """The person hung up: there is nobody left to talk to, so the call is over."""
        if participant.identity != self._leg:
            return
        self._holding.room.off(LEFT, self._somebody_left)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        # The room's callback is synchronous and the ending is not; the task is held so nothing
        # collects it before the log has its last two entries.
        self._ending_it = asyncio.ensure_future(self._ending.hangup(TRANSFERRED))

    # interrupt raises when nothing is playing (agent_activity.py): the agent being quiet already
    # is the state this was asking for.
    async def _cut_the_sentence(self) -> None:
        """The agent's sentence cut where it stands, or nothing when there was none to cut."""
        try:
            await self._live.interrupt(force=True)
        except Exception:
            logger.debug("nothing was playing when the warm transfer took the line")
