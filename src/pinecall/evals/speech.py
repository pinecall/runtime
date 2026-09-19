"""A synthetic caller's own voice: the box's speech tool, as PCM at the rate a room is opened at."""

from __future__ import annotations

import asyncio
import shutil
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from livekit import rtc

# The rate everything downstream is written for: LiveKit's own examples publish at 48 kHz mono.
# Every line comes back at this rate, whatever the tool wrote, so a caller's track can be opened
# before a word of it exists.
SAMPLE_RATE = 48_000
CHANNELS = 1

# macOS first, because it is the box this was measured on, over five calls. `--data-format`
# writes 16-bit little-endian at the rate asked for, and a `.wav` path makes it a WAVE file.
SAY = "say"

# The caller speaks the agent's language, in a voice of that language: an English line read by a
# Spanish voice was heard by the agent's ears as Spanish nonsense, and the whole call went wrong
# from the first turn (2026-09-19, maravilla). `say` names a person per language; espeak-ng takes
# the language itself. A language `say` has nobody for is read by the machine's own voice.
SAY_VOICES = {"es": "Mónica", "en": "Samantha"}

# What a call is spoken in when the agent declared no language: the one the examples were
# measured on, and the television's.
UNDECLARED = "es"

# Linux, where `say` is not: espeak-ng writes a WAVE at its own rate and is resampled below.
ESPEAK = "espeak-ng"

# What the sports bulletin of those calls was: a second voice, reading something nobody is listening
# to. It is spoken by the same tool as the caller, so a box that can make one can make the other.
A_TELEVISION = (
    "A continuación, el resumen deportivo de la jornada. El equipo local venció por dos goles a "
    "uno en un partido disputado hasta el último minuto, y el entrenador destacó el esfuerzo de "
    "sus jugadores en la rueda de prensa posterior al encuentro."
)

NO_SPEECH_TOOL = (
    "this box has no speech tool to give the caller a voice: `say` (macOS) or `espeak-ng` (Linux)"
)


class NoVoice(RuntimeError):
    """Nothing on this box can turn a line into audio, so no caller can be put on a line."""


def a_speech_tool() -> str | None:
    """Which of the two this box has, or None when it has neither."""
    return next((tool for tool in (SAY, ESPEAK) if shutil.which(tool) is not None), None)


async def spoken(text: str, *, language: str | None = None) -> bytes:
    """One line said out loud by the box in that language, as 16-bit mono PCM at SAMPLE_RATE."""
    tool = a_speech_tool()
    if tool is None:
        raise NoVoice(NO_SPEECH_TOOL)
    with TemporaryDirectory() as folder:
        written = Path(folder) / "said.wav"
        await _run(the_command(tool, text, written, language))
        return pcm_of(written)


def the_command(tool: str, text: str, written: Path, language: str | None) -> list[str]:
    """The one command line each tool takes, in the language's voice, at the rate it can be."""
    # `es-ES`, `en_US` and `en` are one language to a voice: the primary subtag is what is matched.
    spoken_in = (language or UNDECLARED).replace("_", "-").split("-")[0].lower()
    if tool == SAY:
        voice = SAY_VOICES.get(spoken_in)
        chosen = [] if voice is None else ["-v", voice]
        return [SAY, *chosen, "-o", str(written), f"--data-format=LEI16@{SAMPLE_RATE}", text]
    return [ESPEAK, "-v", spoken_in, "-w", str(written), text]


async def _run(command: list[str]) -> None:
    """The speech tool, run to the end. What it printed on failure is what the caller is told."""
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, complained = await process.communicate()
    if process.returncode != 0:
        refused = complained.decode(errors="ignore").strip()
        raise NoVoice(f"{command[0]} refused to speak: {refused}")


# The rate is read off the file rather than assumed: `say` gives the one it was asked for and
# espeak-ng writes at whatever it likes, which is brought to SAMPLE_RATE by livekit's own resampler.
def pcm_of(written: Path) -> bytes:
    """The samples of a WAVE the tool just wrote, mono 16-bit, at the room's rate."""
    with wave.open(str(written), "rb") as sound:
        pcm, rate = sound.readframes(sound.getnframes()), sound.getframerate()
    return pcm if rate == SAMPLE_RATE else _at_the_rooms_rate(pcm, rate)


def _at_the_rooms_rate(pcm: bytes, rate: int) -> bytes:
    """The same samples at SAMPLE_RATE, through the resampler the room itself would use."""
    resampler = rtc.AudioResampler(rate, SAMPLE_RATE, num_channels=CHANNELS)
    frame = rtc.AudioFrame(
        data=pcm, sample_rate=rate, num_channels=CHANNELS, samples_per_channel=len(pcm) // 2
    )
    resampled = [*resampler.push(frame), *resampler.flush()]
    return b"".join(bytes(one.data) for one in resampled)
