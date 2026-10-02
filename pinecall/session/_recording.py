"""A call's audio recorded by its own session: the caller left, every other voice right."""

import asyncio
import time
from pathlib import Path

import av
import numpy as np
from av.audio.stream import AudioStream
from livekit import rtc
from livekit.agents import AgentSession
from livekit.agents.voice.recorder_io import RecorderIO

from pinecall.session.hold import VOLUME, HoldMusic
from pinecall.session.room import CallRoom

# livekit's recorder wraps the session's audio in and out and places both on one timeline: the
# caller on the left, the agent on the right, encoded in a thread of this process. Egress did the
# same work in a process of its own per call (~0.12 vCPU, ~80 MB), and a track egress per voice
# costs more still (~0.1 vCPU and ~170 MB a track, infra/lab/ 2026-10-02): the voices are already
# decoded here, so the recording is made here. A supervisor who takes over and the far end of a
# transfer are the room's, not the session's: each is heard from its own track while it speaks,
# and the hold melody is laid in from its clip, both on the right, when the file is closed. The
# session closes the recorder before the seal, so the file is whole when the summary names it.

# The agent's voice comes at 24 kHz and a phone at 8: 48 keeps nothing more (~10 % of a worker's
# cores less, infra/lab/).
RATE = 24000

# Opus takes whole frames of 20 ms (480 samples at RATE).
OPUS_FRAME = 480

# A sum of voices can pass full scale; it is clipped, not wrapped.
FULL_SCALE = 1.0

# Who in the room speaks into the recording besides the session's own two.
OTHERS = frozenset({"supervisor", "sip"})


class Recorder:
    """The call's file: livekit's recorder for its two sides, the room's other voices beside."""

    def __init__(self, recorder: RecorderIO, audio: Path, where: CallRoom | None) -> None:
        """A recording that started now and heard nobody else yet."""
        self.recorder = recorder
        self.audio = audio
        self.where = where
        self.others: list[tuple[float, bytearray]] = []
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
        played = [] if melody is None else melody.played
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
    # placed where its first frame arrived.
    async def _heard(self, track: rtc.RemoteAudioTrack) -> None:
        began = self.recorder.recording_started_at or time.time()
        heard: bytearray | None = None
        stream = rtc.AudioStream(track, sample_rate=RATE, num_channels=1)
        try:
            async for event in stream:
                if heard is None:
                    heard = bytearray()
                    self.others.append((time.time() - event.frame.duration - began, heard))
                heard.extend(event.frame.data)
        finally:
            await stream.aclose()


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


# Blocking, for a thread: the file decoded once, the voices and the melody added on the right,
# encoded once, put in its place.
def laid_in(
    audio: Path,
    others: list[tuple[float, bytearray]],
    melody: tuple[Path | None, list[tuple[float, float]]],
) -> None:
    """Add the other voices and the melody to the recording's right channel, each at its time."""
    both = _decoded(audio, "stereo")
    right: list[tuple[int, np.ndarray]] = []
    for at_s, pcm in others:
        voice = np.frombuffer(bytes(pcm), dtype="<i2").astype(np.float32) / 32768
        right.append((round(at_s * RATE), voice))
    clip, spans = melody
    if clip is not None and spans:
        tune = _decoded(clip, "mono")[0] * VOLUME
        for start_s, end_s in spans:
            length = max(round((end_s - start_s) * RATE), 0)
            right.append((round(start_s * RATE), np.resize(tune, length)))
    length = max([both.shape[1], *(max(at, 0) + len(voice) for at, voice in right)])
    length += -length % OPUS_FRAME
    mixed = np.zeros((2, length), dtype=np.float32)
    mixed[:, : both.shape[1]] = both
    for at, voice in right:
        start = max(at, 0)
        piece = voice[start - at :]
        mixed[1, start : start + len(piece)] += piece
    np.clip(mixed, -FULL_SCALE, FULL_SCALE, out=mixed)
    part = audio.with_name(f"{audio.name}.part")
    encoded(mixed, part)
    part.replace(audio)


def encoded(mixed: np.ndarray, out: Path) -> None:
    """Write two channels of floats at RATE as a stereo Ogg Opus."""
    with av.open(str(out), mode="w", format="ogg") as sink:
        stream: AudioStream = sink.add_stream("libopus", rate=RATE)  # pyright: ignore[reportUnknownMemberType]
        stream.layout = "stereo"
        for start in range(0, mixed.shape[1], OPUS_FRAME):
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(mixed[:, start : start + OPUS_FRAME]),
                format="fltp",
                layout="stereo",
            )
            frame.sample_rate = RATE
            frame.pts = start
            for packet in stream.encode(frame):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]
        for packet in stream.encode(None):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]


def _decoded(audio: Path, layout: str) -> np.ndarray:
    resampler = av.AudioResampler(format="fltp", layout=layout, rate=RATE)
    pieces: list[np.ndarray] = []
    with av.open(str(audio)) as source:
        for frame in source.decode(audio=0):
            pieces.extend(piece.to_ndarray() for piece in resampler.resample(frame))
        pieces.extend(piece.to_ndarray() for piece in resampler.resample(None))
    channels = 2 if layout == "stereo" else 1
    if not pieces:
        return np.zeros((channels, 0), dtype=np.float32)
    return np.concatenate(pieces, axis=1).astype(np.float32)
