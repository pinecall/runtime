"""One simulated caller on a real line: a room, the agent dispatched into it, and a voice on it."""

from __future__ import annotations

import asyncio
import random as randomness
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from livekit import rtc
from livekit.agents.utils import http_context

from pinecall._settings import Settings
from pinecall.auth.scopes import mint_room_token, secret_for
from pinecall.evals import caller_voice, line_noise
from pinecall.evals.agent_dispatch import dispatch_agent
from pinecall.evals.caller_voice import Speaking, Voice
from pinecall.types import Env

# The identity the caller joins under. `pinecall-runtime worker talk` is livekit's console and
# opens the machine's own microphone; this caller never does — its microphone is a file the box
# wrote a moment ago, which is what makes a `--voice` run safe to start from a card.
A_SIMULATED_CALLER = "simulated_caller"

# How long the caller waits for the dispatched job to be listening before giving up. A worker that
# is not running is the usual reason, and it is worth saying so rather than holding an empty room.
# Measured: a cold box whose room connection needed one retry took 15s to have ears.
THE_AGENT_MAY_TAKE_S = 40.0

# How long the caller's seat in the room is good for: longer than any run holds a line, so the
# token never ends a call that the run's own deadlines had not.
A_CALL_MAY_LAST_S = 15 * 60.0

NOBODY_ANSWERED = "no agent joined room {call} in {seconds:.0f}s: is `pinecall-runtime worker` up?"


@dataclass
class Line:
    """How the caller's line is spoiled on purpose, and what a report says about it."""

    # None is a clean line: no interferer is synthesized and nothing is mixed into the caller.
    interferer_db: float | None = None
    packet_loss: float = 0.0
    interferer: bytes = b""
    random: randomness.Random = field(default_factory=randomness.Random)

    @property
    def said(self) -> str:
        """The one sentence a report prints about the line this call was held on."""
        if self.interferer_db is None and self.packet_loss <= 0:
            return "a clean line"
        return line_noise.said_of(self.interferer_db or 0.0, self.packet_loss)


type NextLine = Callable[[int], Awaitable[tuple[str, bool]]]
"""What the caller says next, asked of whoever holds the persona: the line, and the hangup.

It is given how many turns are left and nothing else: the conversation so far is in the call's own
log, and whoever holds the persona reads it back from there — the same reason ring 4's judges read
the log rather than a session's history (docs/decisions/scoring.md)."""


# What the caller does after EVERY line it says — between two of them, and after the last one
# before the line drops. It is given how many lines have been said so far and waits for the agent
# to have finished answering them: a golden that expects a tool on its last turn would otherwise be
# judged on a call that ended mid-thought, and its SECOND line would be said over the answer to its
# first. There is no clock to fall back on: a caller that guesses talks over the agent.
type Settled = Callable[[int], Awaitable[None]]


class Speaks(Protocol):
    """Whoever says the caller's lines out loud: the room's mouth, or a test's notebook."""

    async def say(self, text: str) -> None:
        """One line, said."""
        ...


async def run_simulated_call(
    call: str,
    agent: str,
    *,
    turns: int,
    next_line: NextLine,
    line: Line,
    settings: Settings,
    settled: Settled,
    org: str,
    env: Env,
    holder: str | None = None,
    caller: str | None = None,
    run: str | None = None,
    persona: str | None = None,
    app: str | None = None,
    speaking: Speaking | None = None,
    accepts_when: str | None = None,
    declines_when: str | None = None,
) -> int:
    """Open the room, dispatch the agent into it, say the turns out loud, and hang up.

    `speaking` is how the caller sounds: the agent's language, in a voice that is not the agent's
    unless the persona declared one. The two `*_when` are the persona's own rule for the call.
    """
    async with (
        _the_callers_voice(settings, line, speaking or Speaking()) as voice,
        dispatch_agent(
            call,
            agent,
            settings,
            caller,
            run,
            persona,
            app,
            org,
            env,
            holder,
            accepts_when=accepts_when,
            declines_when=declines_when,
        ),
    ):
        room = rtc.Room()
        await room.connect(settings.livekit_url, _a_token(call, settings))
        try:
            # In the order of a phone call: the caller is on the line, then somebody picks up, then
            # the caller speaks. The line is held open until the run says the answer has landed.
            mouth = await _Mouth.on(room, line, voice)
            await _until_the_agent_is_here(room, call)
            spoken = await every_turn(mouth, turns, next_line, settled)
            # Held once more before the hangup, which ENDS the job: a caller that said it was
            # leaving broke out of `every_turn` without waiting, and the agent's answer to that
            # last line would be cut mid-word — and cut out of the recording with it. `settled`
            # answers at once when the agent is already listening, so a call that is over pays
            # nothing, and it gives up on its own deadline rather than raising (polling.py).
            await settled(spoken)
            return spoken
        finally:
            await room.disconnect()


# The caller's voice for the length of the call, and the television's before it when the line is
# spoiled. The vendor's plugin needs livekit's http session, which only a job binds; this call is
# held outside one, so it opens its own for as long as the call lasts (utils/http_context.py:117).
@asynccontextmanager
async def _the_callers_voice(
    settings: Settings, line: Line, speaking: Speaking
) -> AsyncGenerator[Voice]:
    """The voice the caller's lines are read in, open for the call and let go after it."""
    async with http_context.open():
        if line.interferer_db is not None and not line.interferer:
            television = Voice.of_a_television(settings, speaking.brought)
            try:
                line.interferer = await television.spoken(caller_voice.A_TELEVISION)
            finally:
                await television.aclose()
        voice = Voice.of_the_caller(settings, speaking)
        try:
            yield voice
        finally:
            await voice.aclose()


class _Mouth:
    """The caller's own track: published once, at the room's rate, before a word of it exists."""

    def __init__(self, source: rtc.AudioSource, line: Line, voice: Voice) -> None:
        self._source = source
        self._line = line
        self._voice = voice

    # Published BEFORE the agent is waited for. A room's audio flows because somebody subscribed
    # to a track, and a subscription is signalled, negotiated and then opened: a track opened and
    # pushed into in one breath handed the agent a line already playing, and the 1.7 seconds it took
    # to subscribe were the caller's whole first sentence (2026-09-11, `identifica-al-paciente`, one
    # run in three). The three lines are livekit's own (examples/primitives/echo-agent.py:45-50).
    @classmethod
    async def on(cls, room: rtc.Room, line: Line, voice: Voice) -> _Mouth:
        """The caller's microphone in this room: open, silent, and kept for the whole call."""
        source = rtc.AudioSource(
            sample_rate=caller_voice.SAMPLE_RATE, num_channels=caller_voice.CHANNELS
        )
        track = rtc.LocalAudioTrack.create_audio_track(A_SIMULATED_CALLER, source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        return cls(source, line, voice)

    async def say(self, text: str) -> None:
        """One line in the caller's voice, mixed with the interferer, and pushed frame by frame."""
        pcm = await self._voice.spoken(text)
        if self._line.interferer_db is not None:
            pcm = line_noise.mix_interferer(pcm, self._line.interferer, self._line.interferer_db)
        frames = line_noise.drop_packets(
            line_noise.frames_of(pcm, caller_voice.SAMPLE_RATE),
            self._line.packet_loss,
            self._line.random,
        )
        # No sleep between frames: livekit's own publisher pushes them straight into the source and
        # lets it pace them (examples/primitives/echo-agent.py:94), and its queue is a second of
        # audio. Sleeping here as well would put the caller on the line at half speed.
        for frame in frames:
            await self._source.capture_frame(
                rtc.AudioFrame(
                    data=frame,
                    sample_rate=caller_voice.SAMPLE_RATE,
                    num_channels=caller_voice.CHANNELS,
                    samples_per_channel=len(frame) // 2,
                )
            )


# A talk token, because the caller publishes a microphone: a participate token reads a call and
# publishes nothing, which is exactly what the grants table says of it.
def _a_token(call: str, settings: Settings) -> str:
    """The caller's seat in the room, signed with the pair every call token is signed with."""
    return mint_room_token(
        call, "talk", time.time() + A_CALL_MAY_LAST_S, secret_for(settings), A_SIMULATED_CALLER
    )


# Being in the room is not being ready to hear: the job joins, and its session — the ears, the
# model and the voice — is built after. On a box whose media plane needed a retry the gap was 15
# seconds, and a caller that talked into it said both its lines to nobody. The agent's own audio
# track is what says the pipeline is live, so that is what is waited for.
async def _until_the_agent_is_here(room: rtc.Room, call: str) -> None:
    """Wait for the dispatched job to be LISTENING, or say plainly that nobody answered."""
    published = asyncio.Event()

    def heard(*_: object) -> None:
        if _listening(room):
            published.set()

    # The room says so itself the moment a track is published (livekit's own job waits for a
    # participant the same way); a glance every 250 ms was the same wait, later.
    room.on("track_published", heard)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
    try:
        if _listening(room):
            return
        await asyncio.wait_for(published.wait(), THE_AGENT_MAY_TAKE_S)
    except TimeoutError:
        raise TimeoutError(
            NOBODY_ANSWERED.format(call=call, seconds=THE_AGENT_MAY_TAKE_S)
        ) from None
    finally:
        room.off("track_published", heard)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`


def _listening(room: rtc.Room) -> bool:
    """Whether somebody in the room has published audio: the agent's own voice, which it publishes
    when its session starts. Presence alone is a job that has joined and is still building."""
    return any(
        publication.kind == rtc.TrackKind.KIND_AUDIO
        for participant in room.remote_participants.values()
        for publication in participant.track_publications.values()
    )


async def every_turn(mouth: Speaks, turns: int, next_line: NextLine, settled: Settled) -> int:
    """The caller's turns, said out loud one at a time, each waiting for the answer to the last."""
    spoken = 0
    # Nobody talks over a greeting: the first line waits for the agent's opening, as every later
    # one waits for its answer.
    await settled(0)
    for turn in range(turns):
        text, hanging_up = await next_line(turns - turn)
        if not text:
            break
        await mouth.say(text)
        spoken += 1
        if hanging_up:
            break
        # A caller that guessed said its second line over the answer to its first:
        # `reserva-cuando-el-paciente-dice-que-si` was heard once out of twice, every spoken run,
        # until this waited for the agent instead of six seconds (2026-09-11).
        await settled(spoken)
    return spoken
