"""Hold music: the clip played into the room while the caller waits, faded in and out."""

import asyncio
import io
import logging
from dataclasses import dataclass
from pathlib import Path

import av
from av.audio.fifo import AudioFifo
from av.audio.stream import AudioStream
from av.container import InputContainer, OutputContainer
from av.error import FFmpegError
from livekit import rtc
from livekit.agents.voice.background_audio import AudioConfig, BackgroundAudioPlayer, PlayHandle

from pinecall.domain.errors import DeclarationRefused

logger = logging.getLogger(__name__)

# The hold melody sits under the agent's voice, since a phone leg has no volume of its own. A
# tool that answers inside the grace plays nothing.
VOLUME = 0.6

GRACE_S = 2.5

FADE_IN_S = 0.4

FADE_OUT_S = 0.3


# livekit mixes at 48 kHz; mono like a phone leg; Opus for size, in whole frames only.
RATE = 48_000


OPUS_FRAME = 960


BIT_RATE = 64_000


# The melody loops, so a long file only costs storage, and a very short one stutters.
LONGEST_S = 300


SHORTEST_S = 1


NOT_AUDIO = "that file is no audio this box can read: send a wav, an mp3, an ogg or an m4a"


@dataclass(frozen=True)
class Converted:
    """An upload as the worker plays it: Ogg Opus, 48 kHz mono, and how long it lasts."""

    audio: bytes
    seconds: float


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


TOO_LONG = f"a hold melody loops, so {LONGEST_S // 60} minutes at most: this one runs longer"


TOO_SHORT = f"a hold melody of less than {SHORTEST_S} second would stutter as it loops"


# Converted once, here, so no worker decodes a tenant's upload in the middle of a call.
def converted(data: bytes) -> Converted:
    """Any audio PyAV decodes, as the melody the worker plays; refused in the person's words."""
    try:
        source = av.open(io.BytesIO(data), mode="r")
    except (FFmpegError, OSError, ValueError) as unreadable:
        raise DeclarationRefused(NOT_AUDIO) from unreadable
    with source:
        if not source.streams.audio:
            raise DeclarationRefused(NOT_AUDIO)
        try:
            audio, samples = _encoded(source)
        except (FFmpegError, OSError, ValueError) as unreadable:
            raise DeclarationRefused(NOT_AUDIO) from unreadable
    seconds = samples / RATE
    if seconds < SHORTEST_S:
        raise DeclarationRefused(TOO_SHORT)
    return Converted(audio=audio, seconds=round(seconds, 2))


def _encoded(source: InputContainer) -> tuple[bytes, int]:
    out = io.BytesIO()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    fifo = av.AudioFifo()
    samples = 0
    with av.open(out, mode="w", format="ogg") as sink:
        stream: AudioStream = sink.add_stream("libopus", rate=RATE)  # pyright: ignore[reportUnknownMemberType]
        stream.layout = "mono"
        stream.bit_rate = BIT_RATE
        for decoded in source.decode(audio=0):
            for frame in resampler.resample(decoded):
                samples += frame.samples
                if samples > LONGEST_S * RATE:
                    raise DeclarationRefused(TOO_LONG)
                frame.pts = None
                fifo.write(frame)
            _drained(fifo, stream, sink, final=False)
        for frame in resampler.resample(None):
            samples += frame.samples
            frame.pts = None
            fifo.write(frame)
        _drained(fifo, stream, sink, final=True)
        for packet in stream.encode(None):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]
    return out.getvalue(), samples


# Opus takes whole frames only: a tail shorter than one is dropped.
def _drained(fifo: AudioFifo, stream: AudioStream, sink: OutputContainer, *, final: bool) -> None:
    while fifo.samples >= OPUS_FRAME or (final and fifo.samples > 0):
        frame = fifo.read(min(OPUS_FRAME, fifo.samples))
        if frame is None or frame.samples < OPUS_FRAME:
            return
        for packet in stream.encode(frame):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]
