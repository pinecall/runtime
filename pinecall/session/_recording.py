"""A call's audio recorded by its own session: the caller left, every other voice right."""

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
from av.audio.stream import AudioStream
from livekit import rtc
from livekit.agents import AgentSession
from livekit.agents.voice.recorder_io import RecorderIO

from pinecall.session.hold import VOLUME, HoldMusic, drained
from pinecall.session.room import CallRoom

logger = logging.getLogger(__name__)

# livekit's recorder wraps the session's audio in and out and places both on one timeline: the
# caller on the left, the agent on the right, encoded in a thread of this process. Egress did the
# same work in a process of its own per call (~0.12 vCPU, ~80 MB), and a track egress per voice
# costs more still (~0.1 vCPU and ~170 MB a track, the load lab 2026-10-02): the voices are already
# decoded here, so the recording is made here. A supervisor who takes over and the far end of a
# transfer are the room's, not the session's: each is heard from its own track while it speaks and
# spooled beside the file, and with the hold melody (from its clip, where it sounded) they are laid
# in on the right when the file is closed: the file decoded and encoded once more, a stretch at a
# time. That costs about a fiftieth of the call's length on one core (ten minutes in ~13 s on a
# laptop's), within the worker's sealing budget for the calls a phone line takes; a call with no
# other voice and no hold is never touched again. The session closes the recorder before the seal,
# so the file is whole when the summary names it.

# The agent's voice comes at 24 kHz and a phone at 8: 48 keeps nothing more (~10 % of a worker's
# cores less, the load lab).
RATE = 24000

# Opus takes whole frames of 20 ms (480 samples at RATE).
OPUS_FRAME = 480

# A sum of voices can pass full scale; it is clipped, not wrapped.
FULL_SCALE = 1.0

# Who in the room speaks into the recording besides the session's own two.
OTHERS = frozenset({"supervisor", "sip"})

LOST = "a voice in the room of %s is not in its recording"


@dataclass(frozen=True)
class Sound:
    """One sound laid on the right: the sample it starts at, how many, and any stretch of it."""

    start: int
    length: int
    stretch: Callable[[int, int], np.ndarray]


class Recorder:
    """The call's file: livekit's recorder for its two sides, the room's other voices beside."""

    def __init__(self, recorder: RecorderIO, audio: Path, where: CallRoom | None) -> None:
        """A recording that started now and heard nobody else yet."""
        self.recorder = recorder
        self.audio = audio
        self.where = where
        self.others: list[tuple[float, Path]] = []
        self.tasks: set[asyncio.Task[None]] = set()

    def listen(self) -> None:
        """Hear every other voice the room has, and each one that joins from now on."""
        if self.where is None:
            return
        room = self.where.room
        room.on("track_subscribed", self._subscribed)  # pyright: ignore[reportUnknownMemberType]
        for seat in room.remote_participants.values():
            for publication in seat.track_publications.values():
                if publication.track is not None:
                    self._subscribed(publication.track, publication, seat)

    async def close(self, melody: HoldMusic | None) -> None:
        """Finish the file; lay in the other voices and the melody when there were any."""
        if self.where is not None:
            self.where.room.off("track_subscribed", self._subscribed)  # pyright: ignore[reportUnknownMemberType]
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.recorder.aclose()
        began = self.recorder.recording_started_at
        played = [] if melody is None else melody.sounded()
        if began is None or (not self.others and not played):
            return
        spans = [(start - began, end - began) for start, end in played]
        clip = None if melody is None else melody.clip
        await asyncio.to_thread(laid_in, self.audio, self.others, (clip, spans))

    def _subscribed(
        self, track: rtc.Track, _publication: rtc.TrackPublication, seat: rtc.RemoteParticipant
    ) -> None:
        if self.where is None or not isinstance(track, rtc.RemoteAudioTrack):
            return
        if self.where.kind_of(seat, dict(seat.attributes)) not in OTHERS:
            return
        task = asyncio.create_task(self._heard(track))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # The track's frames come continuous (the jitter buffer fills a silence), so one run a voice,
    # spooled as it comes and placed where its first frame arrived. A run that breaks is said and
    # kept as far as it got: the recording is the call's, with or without that voice.
    async def _heard(self, track: rtc.RemoteAudioTrack) -> None:
        began = self.recorder.recording_started_at or time.time()
        spool = self.audio.with_name(f"{track.sid}.pcm")
        stream = rtc.AudioStream(track, sample_rate=RATE, num_channels=1)
        try:
            with spool.open("wb") as sink:
                async for event in stream:
                    if sink.tell() == 0:
                        self.others.append((time.time() - event.frame.duration - began, spool))
                    sink.write(event.frame.data)
        except Exception:
            logger.warning(LOST, self.audio.parent.name, exc_info=True)
        finally:
            await stream.aclose()


# A stretch of silence laid in past the file's end, at a time.
STRETCH = RATE


async def recorded(
    live: AgentSession[None], audio: Path | None, where: CallRoom | None
) -> Recorder | None:
    """Record the call into the file from now on; None when it keeps no audio."""
    caller, agent = live.input.audio, live.output.audio
    if audio is None or caller is None or agent is None:
        return None
    recorder = RecorderIO(agent_session=live, sample_rate=RATE)
    live.input.audio = recorder.record_input(caller)
    live.output.audio = recorder.record_output(agent)
    await recorder.start(output_path=audio)
    recording = Recorder(recorder, audio, where)
    recording.listen()
    return recording


# Blocking, for a thread: the file decoded a frame at a time, the sounds added on the right where
# each falls, encoded as it goes, put in its place. Nothing of the call's length is held at once.
def laid_in(
    audio: Path,
    others: list[tuple[float, Path]],
    melody: tuple[Path | None, list[tuple[float, float]]],
) -> None:
    """Add the other voices and the melody to the recording's right channel, each at its time."""
    sounds = [*(_voice(at_s, pcm) for at_s, pcm in others), *_tune(*melody)]
    part = audio.with_name(f"{audio.name}.part")
    encoded(_mixed(_decoded(audio, "stereo"), sounds), part)
    part.replace(audio)
    for _, pcm in others:
        pcm.unlink(missing_ok=True)


def encoded(stretches: Iterable[np.ndarray], out: Path) -> None:
    """Write stretches of two channels of floats at RATE as one stereo Ogg Opus."""
    fifo = av.AudioFifo()
    with av.open(str(out), mode="w", format="ogg") as sink:
        stream: AudioStream = sink.add_stream("libopus", rate=RATE)  # pyright: ignore[reportUnknownMemberType]
        stream.layout = "stereo"
        for stretch in stretches:
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(stretch), format="fltp", layout="stereo"
            )
            frame.sample_rate = RATE
            frame.pts = None
            fifo.write(frame)
            drained(fifo, stream, sink, final=False)
        drained(fifo, stream, sink, final=True)
        for packet in stream.encode(None):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]


def _voice(at_s: float, pcm: Path) -> Sound:
    samples = np.memmap(pcm, dtype="<i2", mode="r")

    def stretch(at: int, count: int) -> np.ndarray:
        return samples[at : at + count].astype(np.float32) / 32768

    return Sound(round(at_s * RATE), len(samples), stretch)


# The melody loops from the start of each span, as the player played it.
def _tune(clip: Path | None, spans: list[tuple[float, float]]) -> list[Sound]:
    pieces = [] if clip is None else list(_decoded(clip, "mono"))
    if not pieces or not spans:
        return []
    tune = np.concatenate(pieces, axis=1)[0] * VOLUME

    def stretch(at: int, count: int) -> np.ndarray:
        return np.take(tune, np.arange(at, at + count) % len(tune))

    return [
        Sound(round(start_s * RATE), max(round((end_s - start_s) * RATE), 0), stretch)
        for start_s, end_s in spans
    ]


def _mixed(stretches: Iterator[np.ndarray], sounds: list[Sound]) -> Iterator[np.ndarray]:
    cursor = 0
    end = max((sound.start + sound.length for sound in sounds), default=0)
    for stretch in stretches:
        yield _laid(stretch, cursor, sounds)
        cursor += stretch.shape[1]
    while cursor < end:
        count = min(STRETCH, end - cursor)
        yield _laid(np.zeros((2, count), dtype=np.float32), cursor, sounds)
        cursor += count


def _laid(stretch: np.ndarray, cursor: int, sounds: list[Sound]) -> np.ndarray:
    until = cursor + stretch.shape[1]
    for sound in sounds:
        low, high = max(sound.start, cursor), min(sound.start + sound.length, until)
        if low < high:
            stretch[1, low - cursor : high - cursor] += sound.stretch(low - sound.start, high - low)
    np.clip(stretch, -FULL_SCALE, FULL_SCALE, out=stretch)
    return stretch


def _decoded(audio: Path, layout: str) -> Iterator[np.ndarray]:
    resampler = av.AudioResampler(format="fltp", layout=layout, rate=RATE)
    with av.open(str(audio)) as source:
        for frame in source.decode(audio=0):
            for piece in resampler.resample(frame):
                yield piece.to_ndarray()
        for piece in resampler.resample(None):
            yield piece.to_ndarray()
