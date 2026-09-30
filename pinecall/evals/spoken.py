"""A persona on a real line: the caller's leg in the room, its lines spoken, the noise on it."""

import asyncio
import math
import random as randomness
import time
from array import array
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Never

from livekit import rtc
from livekit.agents import tts
from livekit.agents.utils import http_context

from pinecall.log.logs import Logs, Subscription
from pinecall.providers.build import primary
from pinecall.wire.events import AgentStateChanged
from pinecall.wire.frames import Entry

# The caller's next line and whether it hangs up after it, given the turns left.
type NextLine = Callable[[int], Awaitable[tuple[str, bool]]]


# 10 ms of 16-bit mono per frame, the cadence livekit's examples publish at.
FRAME_MS = 10


# A sum past 16 bits is clipped, never wrapped: a wrap is a click the ears would hear.
LOUDEST = 32767


QUIETEST = -32768


# A lost packet is the silence a jitter buffer plays in its place.
LOST = b"\x00\x00"


# A television 15 dB under the caller was already transcribed as the caller.
UNDER_THE_CALLER_DB = 15.0


# Every line is resampled to this rate, so the caller's track opens before any audio exists.
SAMPLE_RATE = 48_000


CHANNELS = 1


A_SIMULATED_CALLER = "simulated_caller"


# A cold worker has been measured at 15 s before it publishes its audio.
THE_AGENT_MAY_TAKE_S = 40.0


# A turn with a tool speaks twice: two rounds of speech measured about 13 s.
AN_ANSWER_MAY_TAKE_S = 30.0


# Longer than the lag between a state and its entry, shorter than a caller's pause.
A_BEAT_S = 0.75


# An agent that only listens this long opens with nothing.
A_SILENT_OPENING_S = 3.0


AN_OPENING_MAY_TAKE_S = 15.0


NOBODY_ANSWERED = "no agent joined room {call} in {seconds:.0f}s: is the world's fleet up?"


# The background voice a noisy line carries.
A_TELEVISION = (
    "A continuación, el resumen deportivo de la jornada. El equipo local venció por dos goles a "
    "uno en un partido disputado hasta el último minuto, y el entrenador destacó el esfuerzo de "
    "sus jugadores en la rueda de prensa posterior al encuentro."
)


LISTENING = "listening"


@dataclass(frozen=True)
class Line:
    """What the line does to the caller's voice: a voice under it, and packets lost."""

    # None: nothing is mixed in.
    interferer_db: float | None = None
    packet_loss: float = 0.0
    dice: randomness.Random = field(default_factory=randomness.Random, compare=False)


@dataclass(frozen=True)
class SpokenLine:
    """A spoken call as the caller joins it: the room's address, its token, the call, its turns."""

    url: str
    token: str
    call: str
    turns: int
    line: Line


class CallerLeg:
    """The caller's leg of a spoken call: its track in the room, its voice, the line's noise."""

    def __init__(
        self, spoken: SpokenLine, voice: tts.TTS[Never], logs: Logs, interferer: bytes
    ) -> None:
        """A leg with its track made and nothing said yet."""
        self.spoken = spoken
        self.voice = voice
        self.logs = logs
        self.interferer = interferer
        self.source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        self.track = rtc.LocalAudioTrack.create_audio_track(A_SIMULATED_CALLER, self.source)

    async def say(self, line: str) -> None:
        """Speak one line into the room, through the line's noise."""
        pcm = await _synthesized(self.voice, line)
        if self.spoken.line.interferer_db is not None:
            pcm = mix_interferer(pcm, self.interferer, self.spoken.line.interferer_db)
        for frame in drop_packets(frames_of(pcm, SAMPLE_RATE), self.spoken.line):
            await self.source.capture_frame(
                rtc.AudioFrame(frame, SAMPLE_RATE, CHANNELS, len(frame) // 2)
            )

    async def line_answered(self, lines: int) -> None:
        """Wait until the agent answered this many lines, or the call ended."""
        await answered(self.logs, self.spoken.call, lines)


def described(line: Line) -> str:
    """The line in the words a report prints."""
    if line.interferer_db is None and line.packet_loss <= 0:
        return "a clean line"
    noise = f"interferer {line.interferer_db or 0.0:.0f} dB under the caller"
    return noise if line.packet_loss <= 0 else f"{noise}, {line.packet_loss * 100:.0f}% packet loss"


def frames_of(pcm: bytes, rate: int) -> list[bytes]:
    """The audio cut in 10 ms frames, the last padded with silence: a room refuses a short one."""
    size = (rate * FRAME_MS // 1000) * 2
    frames = [pcm[start : start + size] for start in range(0, len(pcm), size)]
    if frames and len(frames[-1]) < size:
        frames[-1] += LOST * ((size - len(frames[-1])) // 2)
    return frames


def rms_of(pcm: bytes) -> float:
    """The level of 16-bit audio."""
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    return math.sqrt(sum(float(sample) * sample for sample in samples) / len(samples))


# The interferer loops, so a short one covers a long line.
def mix_interferer(
    caller: bytes, interferer: bytes, db_under: float = UNDER_THE_CALLER_DB
) -> bytes:
    """The interferer mixed under the caller's voice, `db_under` quieter."""
    if not interferer or not caller:
        return caller
    loudness = rms_of(interferer)
    gain = 0.0 if loudness == 0 else rms_of(caller) / loudness * 10 ** (-db_under / 20)
    voice = array("h")
    voice.frombytes(caller)
    noise = array("h")
    noise.frombytes(interferer)
    for index in range(len(voice)):
        mixed = voice[index] + int(noise[index % len(noise)] * gain)
        voice[index] = max(QUIETEST, min(LOUDEST, mixed))
    return voice.tobytes()


def drop_packets(frames: Sequence[bytes], line: Line) -> list[bytes]:
    """The frames with the line's share of them lost."""
    if line.packet_loss <= 0:
        return list(frames)
    return [
        LOST * (len(frame) // 2) if line.dice.random() < line.packet_loss else frame
        for frame in frames
    ]


def resample_to_room(pcm: bytes, rate: int) -> bytes:
    """The audio at the room's rate, as it was when it already is."""
    if rate == SAMPLE_RATE:
        return pcm
    resampler = rtc.AudioResampler(rate, SAMPLE_RATE, num_channels=CHANNELS)
    frame = rtc.AudioFrame(
        data=pcm, sample_rate=rate, num_channels=CHANNELS, samples_per_channel=len(pcm) // 2
    )
    resampled = [*resampler.push(frame), *resampler.flush()]
    return b"".join(bytes(piece.data) for piece in resampled)


# The operator's voices per language are the ones an agent speaks with when it names none; the
# caller takes one the agent is not using, in its language, else in English, else the plugin's.
def pick_caller_voice(
    voices: Mapping[str, str], vendor: str, agents_voice: str | None, language: str | None
) -> str | None:
    """A voice of the vendor the caller speaks with, never the agent's."""
    own = f"{vendor}/{primary(language) or 'en'}"
    ordered = [voices.get(own), voices.get(f"{vendor}/en")]
    ordered += [voice for key, voice in voices.items() if key.startswith(f"{vendor}/")]
    return next((voice for voice in ordered if voice and voice != agents_voice), None)


def is_call_over(entries: Sequence[Entry]) -> bool:
    """Whether somebody hung up: every end lands as call.ended."""
    return any(entry.type == "call.ended" for entry in entries)


def is_line_open(entries: Sequence[Entry], now: float) -> bool:
    """Whether the agent finished its opening, or has none, and listens."""
    states = [(index, entry) for index, entry in enumerate(entries) if entry.type == "agent.state"]
    if not states:
        return False
    spoke = [index for index, entry in enumerate(entries) if entry.type == "turn.agent"]
    index, last = states[-1]
    if spoke:
        return _state_of(last) == LISTENING and index > spoke[-1]
    only_listened = all(_state_of(entry) == LISTENING for _, entry in states)
    return only_listened and now - states[0][1].ts >= A_SILENT_OPENING_S


# Turn counts lie: livekit speaks a tool's preamble as a turn of its own, and some ears end a
# turn per sentence. The agent back to listening after it took the line is the signal.
def has_answer_landed(entries: Sequence[Entry], lines: int, *, since: float) -> bool:
    """Whether every line reached the agent, and it is listening again after taking the last."""
    heard = [index for index, entry in enumerate(entries) if entry.type == "turn.user"]
    # A snapshot older than the caller's silence would answer with the turn before.
    if not heard or entries[heard[-1]].ts < since or len(heard) < lines:
        return False
    if _is_a_tool_running(entries):
        return False
    states = [(index, entry) for index, entry in enumerate(entries) if entry.type == "agent.state"]
    took_it = any(index > heard[-1] and _state_of(entry) != LISTENING for index, entry in states)
    if not took_it:
        return False
    index, last = states[-1]
    return index > heard[-1] and _state_of(last) == LISTENING


# Read off the log as it grows, never on a timer: a fixed wait talked over tool answers.
async def answered(logs: Logs, call: str, lines: int) -> None:
    """Wait until the agent answered `lines` lines (its opening when 0), or the call ended."""
    since = time.time()
    subscription = await logs.reading(call).followed()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (AN_OPENING_MAY_TAKE_S if lines == 0 else AN_ANSWER_MAY_TAKE_S)
    try:
        while (left := deadline - loop.time()) > 0:
            entries = await logs.store.whole(call)
            done = (
                is_line_open(entries, time.time())
                if lines == 0
                else has_answer_landed(entries, lines, since=since)
            )
            if is_call_over(entries):
                return
            # A state reaches the log late: an answer holds only if a beat passes with nothing.
            if not await _moved(subscription, min(A_BEAT_S, left)) and done:
                return
    finally:
        subscription.close()


async def speak_turns(
    turns: int,
    next_line: NextLine,
    say: Callable[[str], Awaitable[None]],
    wait: Callable[[int], Awaitable[None]],
) -> int:
    """Say each line once the agent answered the last, the first after its opening; the count."""
    spoken = 0
    await wait(0)
    for turn in range(turns):
        line, hanging_up = await next_line(turns - turn)
        if not line:
            break
        await say(line)
        spoken += 1
        if hanging_up:
            break
        await wait(spoken)
    return spoken


# Deleting the room is the door's: it ends the agent's job, which seals the log.
async def run_spoken(
    spoken: SpokenLine, voice: tts.TTS[Never], logs: Logs, next_line: NextLine
) -> int:
    """Join the room as the caller, say the lines as the agent answers, and hang up."""
    room = rtc.Room()
    async with http_context.open():
        interferer = b""
        if spoken.line.interferer_db is not None:
            interferer = await _synthesized(voice, A_TELEVISION)
        await room.connect(spoken.url, spoken.token)
        try:
            leg = CallerLeg(spoken, voice, logs, interferer)
            # Published before the agent arrives: audio pushed before a subscription is lost.
            await room.local_participant.publish_track(
                leg.track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            await _until_the_agent_is_here(room, spoken.call)
            said_count = await speak_turns(spoken.turns, next_line, leg.say, leg.line_answered)
            # Waited again: hanging up at once would cut the agent's last answer.
            await leg.line_answered(said_count)
            return said_count
        finally:
            await room.disconnect()


def _state_of(entry: Entry) -> str:
    return AgentStateChanged.model_validate(entry.data).state


# livekit publishes `listening` while a tool runs, so a call with no result yet is mid-turn.
def _is_a_tool_running(entries: Sequence[Entry]) -> bool:
    called = {str(entry.data.get("call_id")) for entry in entries if entry.type == "tool.call"}
    answered_ids = {
        str(entry.data.get("call_id")) for entry in entries if entry.type == "tool.result"
    }
    return bool(called - answered_ids)


async def _moved(subscription: Subscription, within_s: float) -> bool:
    try:
        await asyncio.wait_for(anext(subscription), within_s)
    except (TimeoutError, StopAsyncIteration):
        return False
    return True


async def _synthesized(voice: tts.TTS[Never], text: str) -> bytes:
    frame = await voice.synthesize(text).collect()
    return resample_to_room(bytes(frame.data), frame.sample_rate)


# The agent joins before its session is built; its published audio means it listens.
async def _until_the_agent_is_here(room: rtc.Room, call: str) -> None:
    def listening() -> bool:
        return any(
            publication.kind == rtc.TrackKind.KIND_AUDIO
            for participant in room.remote_participants.values()
            for publication in participant.track_publications.values()
        )

    for _ in range(int(THE_AGENT_MAY_TAKE_S / A_BEAT_S)):
        if listening():
            return
        await asyncio.sleep(A_BEAT_S)
    raise TimeoutError(NOBODY_ANSWERED.format(call=call, seconds=THE_AGENT_MAY_TAKE_S))
