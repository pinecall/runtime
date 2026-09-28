"""Hold music: the clip played into the room while the caller waits, faded in and out."""

import asyncio
import logging
from pathlib import Path

from livekit import rtc
from livekit.agents.voice.background_audio import AudioConfig, BackgroundAudioPlayer, PlayHandle

logger = logging.getLogger(__name__)

# The hold melody sits under the agent's voice, since a phone leg has no volume of its own. A
# tool that answers inside the grace plays nothing.
VOLUME = 0.6

GRACE_S = 2.5

FADE_IN_S = 0.4

FADE_OUT_S = 0.3


class HoldMusic:
    """The melody played while a tool runs or the line is held, shared by what overlaps."""

    def __init__(self, clip: Path) -> None:
        """A melody not yet in any room."""
        self.clip = clip
        self.player: BackgroundAudioPlayer | None = None
        self.handle: PlayHandle | None = None
        self.running = 0
        self.pending: asyncio.Task[None] | None = None
        self.quiet = asyncio.Event()
        self.quiet.set()

    # The player publishes its own track, which the SIP bridge mixes into the phone leg. A
    # player that does not start leaves the call without a melody, and nothing else.
    async def start(self, room: rtc.Room) -> None:
        """Publish the melody's track in the room."""
        player = BackgroundAudioPlayer()
        try:
            await player.start(room=room)  # pyright: ignore[reportUnknownMemberType]
        except (RuntimeError, ConnectionError):
            logger.warning(
                "the hold melody did not start: the call goes on without it", exc_info=True
            )
            return
        self.player = player

    def floor(self, *, speaking: bool) -> None:
        """Whether the agent is speaking, as the session says."""
        if speaking:
            self.quiet.clear()
        else:
            self.quiet.set()

    def began(self) -> None:
        """One more reason to play; the first starts the melody after the grace."""
        self.running += 1
        if self.running == 1 and self.player is not None:
            self.pending = asyncio.create_task(self._after_the_grace())

    def ended(self) -> None:
        """One reason less; the last stops the melody."""
        self.running = max(self.running - 1, 0)
        if self.running == 0:
            self._stop()

    async def aclose(self) -> None:
        """Stop and close the player."""
        self._stop()
        player, self.player = self.player, None
        if player is not None:
            await player.aclose()

    # The tool starts while its announcement still plays, so the grace counts from when the agent
    # is quiet; checked again after it, since livekit may speak another round's preamble.
    async def _after_the_grace(self) -> None:
        await self.quiet.wait()
        await asyncio.sleep(GRACE_S)
        if self.running == 0 or not self.quiet.is_set() or self.player is None:
            return
        melody = AudioConfig(str(self.clip), volume=VOLUME, fade_in=FADE_IN_S, fade_out=FADE_OUT_S)
        self.handle = self.player.play(melody, loop=True)

    def _stop(self) -> None:
        if self.pending is not None and not self.pending.done():
            self.pending.cancel()
        self.pending = None
        if self.handle is not None:
            self.handle.stop()
            self.handle = None
