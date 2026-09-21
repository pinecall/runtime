"""The hold melody, played into the room while a tool runs: one track beside the agent's voice."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, cast

from livekit import rtc
from livekit.agents import get_job_context
from livekit.agents.voice.background_audio import AudioConfig, BackgroundAudioPlayer, PlayHandle

logger = logging.getLogger(__name__)

# Under the voice that comes back, never over it: a phone leg has no volume knob of its own.
VOLUME = 0.6
# A tool that answers in a moment plays nothing: a note that starts and is cut is worse than none.
# Two and a half seconds and not half of one, because the agent ANNOUNCES the slow tools — it says
# "let me look that up" and the tool starts in the same breath, so a melody on a short grace comes
# up underneath the agent's own voice and the caller hears both at once (2026-09-21, maravilla).
# By this point the announcement has been said and the silence is real.
GRACE_S = 2.5
FADE_IN_S = 0.4
# How often the melody looks to see whether the agent has stopped talking.
A_GLANCE_S = 0.15
FADE_OUT_S = 0.3


# livekit's BackgroundAudioPlayer (voice/background_audio.py) publishes its own track, which the
# widget and the console attach like any other audio and the SIP bridge mixes into the phone leg —
# livekit's warm transfer plays its hold music to a SIP caller exactly this way. Its own
# `thinking_sound` is NOT used: livekit sets "thinking" at the start of every reply, so that sound
# would play through ordinary model latency on every turn, and this is for a tool and only a tool.
class HoldMusic:
    """One call's melody: silent until a tool runs, and never in the way of the call if it fails."""

    def __init__(
        self, source: Path | None = None, speaking: Callable[[], bool] | None = None
    ) -> None:
        self._source = source
        # Whether the agent has the floor right now. A melody is for a silence, and starting one
        # under a voice is worse than either on its own.
        self._speaking = speaking or (lambda: False)
        self._player: BackgroundAudioPlayer | None = None
        self._handle: PlayHandle | None = None
        self._running = 0
        self._pending: asyncio.Task[None] | None = None

    # After the session started, because the player publishes its own track into a room that must
    # already be joined, and closed with the job. A call with no job — a test, a text session — has
    # no room to play it in, and gets a melody that plays nothing.
    @classmethod
    async def in_this_room(
        cls, melody: Path | None, speaking: Callable[[], bool] | None = None
    ) -> HoldMusic:
        """The call's melody, its track published in the job's room; silent without one."""
        job = get_job_context(required=False)
        if melody is None or job is None:
            return cls(None, speaking)
        hold = cls(melody, speaking)
        await hold.start(job.room)
        job.add_shutdown_callback(hold.aclose)
        return hold

    @property
    def source(self) -> Path | None:
        """The file this call plays, or None: turned off, or a call with no room to play it in."""
        return self._source

    async def start(self, room: rtc.Room) -> None:
        """Publish the track. Failing here costs the melody and nothing else."""
        if self._source is None:
            return
        player = BackgroundAudioPlayer()
        try:
            await cast(Any, player).start(room=room)
        except Exception:  # noqa: BLE001 — a melody that cannot start must not end a call
            logger.warning(
                "the hold melody could not start: the call goes on without it", exc_info=True
            )
            return
        self._player = player

    # Tools can run side by side in one step: the melody plays from the first to start until the
    # last to finish, once, rather than stopping under a tool that is still running.
    def playing(self) -> _AroundATool:
        """Around one tool: the melody starts after a grace, and stops when the last tool ends."""
        return _AroundATool(self)

    def began(self) -> None:
        if self._player is None:
            return
        self._running += 1
        if self._running == 1:
            self._pending = asyncio.create_task(self._after_grace())

    def ended(self) -> None:
        if self._player is None or self._running == 0:
            return
        self._running -= 1
        if self._running == 0:
            self._silence()

    # The tool starts while the agent is still saying the line that announced it — the model emits
    # its text and its tool call in one response, so the two overlap by construction. A melody on a
    # timer alone therefore came up underneath the agent's own voice, every single time a tool ran
    # after an announcement. It waits for the floor now: the grace is counted from the moment the
    # agent stops speaking, not from the moment the tool began.
    async def _after_grace(self) -> None:
        while self._running > 0 and self._speaking():
            await asyncio.sleep(A_GLANCE_S)
        if self._running == 0:
            return
        await asyncio.sleep(GRACE_S)
        # And again, because a turn can begin speaking during the grace: livekit speaks the
        # preamble of the round that FOLLOWS a tool result while the next tool of the same reply
        # is already running.
        if self._running == 0 or self._speaking() or self._player is None or self._source is None:
            return
        try:
            self._handle = self._player.play(
                AudioConfig(
                    str(self._source), volume=VOLUME, fade_in=FADE_IN_S, fade_out=FADE_OUT_S
                ),
                loop=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning("the hold melody could not play", exc_info=True)

    def _silence(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None
        if self._handle is not None:
            self._handle.stop()
            self._handle = None

    async def aclose(self) -> None:
        """Unpublish the track, when the call is over."""
        self._silence()
        if self._player is not None:
            player, self._player = self._player, None
            try:
                await player.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("the hold melody's player did not close cleanly", exc_info=True)


class _AroundATool:
    """`async with hold.playing():` — the tool's round trip, with the melody under it."""

    def __init__(self, hold: HoldMusic) -> None:
        self._hold = hold

    async def __aenter__(self) -> None:
        self._hold.began()

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._hold.ended()
