"""The caller on hold: the melody they hear, the ears the agent loses, and the entry for it."""

from __future__ import annotations

import logging
from collections.abc import Callable

from livekit.agents.voice import AgentSession

from pinecall.session.voice.hold_melody import HoldMusic
from pinecall.session.voice.log_writer import Writing
from pinecall_protocol.events import CallLine

logger = logging.getLogger(__name__)

# The agent's own mute: `call.mute` is in the wire and no runtime sets it, so the flag the entry
# carries is the one thing this class knows — a held line is a silent one either way.
NOT_MUTED = False


# Hold is three things at once and they have to move together: the melody comes up, the agent goes
# mute AND deaf — what it cannot hear it cannot later claim to remember, the rule a takeover lives
# by — and `call.line` states both flags so a reader never has to remember the other.
class Line:
    """This call's line: held or not, and the one place that says so to the room and the log."""

    # The melody is asked for rather than held: its track is published once the room is live, which
    # is after the session — and therefore this — was built.
    def __init__(
        self, live: AgentSession[None], writing: Writing, hold: Callable[[], HoldMusic]
    ) -> None:
        self._live = live
        self._writing = writing
        self._hold = hold
        self.held = False

    async def hold(self) -> None:
        """The caller waits: the melody plays, and the agent neither speaks nor hears."""
        if self.held:
            return
        self.held = True
        await silence(self._live, "the line went on hold")
        self._hold().began()
        await self._writing.emit("call.line", CallLine(held=True, muted=NOT_MUTED))

    # `speaks_again` is False when somebody else is taking the line the hold was for: a supervisor
    # who just took over wants the melody gone and the agent exactly as deaf as it already is.
    async def unhold(self, *, speaks_again: bool = True) -> None:
        """The wait is over: the melody stops, and the agent has the call back unless told not."""
        if not self.held:
            return
        self.held = False
        self._hold().ended()
        if speaks_again:
            hearing_again(self._live)
        await self._writing.emit("call.line", CallLine(held=False, muted=NOT_MUTED))


# The hold, a warm transfer answered and a supervisor's takeover all take the agent off the line
# the same way, and a hold and a release give it back the same way: said here once.
async def silence(live: AgentSession[None], why: str) -> None:
    """The agent's sentence cut where it stands, then mute AND deaf: what it cannot hear, it
    cannot later claim to remember. interrupt raises when nothing is playing, or the session has
    already stopped (agent_session.py:1534): the agent being quiet is the state asked for."""
    try:
        await live.interrupt(force=True)
    except Exception:
        logger.debug("nothing was playing when %s", why)
    live.output.set_audio_enabled(False)
    live.input.set_audio_enabled(False)


def hearing_again(live: AgentSession[None]) -> None:
    """Ears before voice: a session that could speak before it could hear would answer into a
    sentence it never heard the start of."""
    live.input.set_audio_enabled(True)
    live.output.set_audio_enabled(True)
