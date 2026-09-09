"""A synthetic caller's own voice: the box's speech tool, as PCM a room can carry."""

from __future__ import annotations

import asyncio
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

# The rate everything downstream is written for: LiveKit's own examples publish at 48 kHz mono, and
# it is what `say` is asked for so that nothing has to resample a caller on the way to the room.
SAMPLE_RATE = 48_000
CHANNELS = 1

# macOS first, because it is the box this was measured on, over five calls. `--data-format`
# writes 16-bit little-endian at the rate asked for, and a `.wav` path makes it a WAVE file.
SAY = "say"
A_SPANISH_VOICE = "Mónica"

# Linux, where `say` is not: espeak-ng writes a WAVE at its own rate, which is read off the file.
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


@dataclass(frozen=True)
class Audio:
    """One line said out loud: the samples, and the rate whoever publishes them must declare."""

    pcm: bytes
    sample_rate: int


def a_speech_tool() -> str | None:
    """Which of the two this box has, or None when it has neither."""
    return next((tool for tool in (SAY, ESPEAK) if shutil.which(tool) is not None), None)


async def spoken(text: str, *, voice: str = A_SPANISH_VOICE) -> Audio:
    """One line said out loud by the box, as 16-bit mono PCM at whatever rate the tool wrote."""
    tool = a_speech_tool()
    if tool is None:
        raise NoVoice(NO_SPEECH_TOOL)
    with TemporaryDirectory() as folder:
        written = Path(folder) / "said.wav"
        await _run(_the_command(tool, text, written, voice))
        return _read(written)


def _the_command(tool: str, text: str, written: Path, voice: str) -> list[str]:
    """The one command line each tool takes, with the rate asked for where it can be."""
    if tool == SAY:
        return [SAY, "-v", voice, "-o", str(written), f"--data-format=LEI16@{SAMPLE_RATE}", text]
    return [ESPEAK, "-v", "es", "-w", str(written), text]


async def _run(command: list[str]) -> None:
    """The speech tool, run to the end. What it printed on failure is what the caller is told."""
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, complained = await process.communicate()
    if process.returncode != 0:
        refused = complained.decode(errors="ignore").strip()
        raise NoVoice(f"{command[0]} refused to speak: {refused}")


# The rate is read off the file rather than assumed: `say` is asked for 48 kHz and gives it, and
# espeak-ng writes at whatever it likes. Whoever publishes this declares the rate it came back with.
def _read(written: Path) -> Audio:
    """The samples of a WAVE the tool just wrote, mono 16-bit, at the rate it chose."""
    with wave.open(str(written), "rb") as sound:
        return Audio(pcm=sound.readframes(sound.getnframes()), sample_rate=sound.getframerate())
