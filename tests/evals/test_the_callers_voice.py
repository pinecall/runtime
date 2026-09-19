"""What the caller's voice comes back as, whatever the box's speech tool wrote: one rate, always."""

import wave
from pathlib import Path

import pytest

from pinecall.evals import speech

pytestmark = pytest.mark.unit

# One second of a tone, written the way a WAVE holds it: 16-bit mono.
A_SECOND_OF_SILENCE_AT = {rate: b"\x00\x00" * rate for rate in (22_050, speech.SAMPLE_RATE)}


def _a_wave_at(rate: int, folder: Path) -> Path:
    """A WAVE file of one second, as a speech tool would leave it, at the rate it chose."""
    written = folder / f"said_{rate}.wav"
    with wave.open(str(written), "wb") as sound:
        sound.setnchannels(speech.CHANNELS)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(A_SECOND_OF_SILENCE_AT[rate])
    return written


def test_what_say_wrote_at_the_rooms_rate_comes_back_untouched(tmp_path: Path) -> None:
    pcm = speech.pcm_of(_a_wave_at(speech.SAMPLE_RATE, tmp_path))

    assert pcm == A_SECOND_OF_SILENCE_AT[speech.SAMPLE_RATE]


# espeak-ng writes at 22 050 Hz and takes no flag to change it. The caller's track is opened at
# the room's rate before a word exists, so what the tool wrote is brought to that rate here, and a
# second of speech is still a second of speech when it is pushed.
def test_what_espeak_wrote_at_its_own_rate_comes_back_as_a_second_at_the_rooms(
    tmp_path: Path,
) -> None:
    pcm = speech.pcm_of(_a_wave_at(22_050, tmp_path))

    samples = len(pcm) // 2
    assert abs(samples - speech.SAMPLE_RATE) < speech.SAMPLE_RATE // 100, (
        "a second in should be a second out, within the resampler's own edge"
    )


# The caller is read in the agent's language. An English line read by the Spanish voice reached
# the agent's ears as Spanish nonsense and the call went wrong from its first turn.
def test_say_reads_an_english_agents_caller_in_an_english_voice(tmp_path: Path) -> None:
    command = speech.the_command(speech.SAY, "hello", tmp_path / "said.wav", "en-US")

    assert command[1:3] == ["-v", "Samantha"]


def test_espeak_is_asked_for_the_agents_language_by_its_primary_subtag(tmp_path: Path) -> None:
    command = speech.the_command(speech.ESPEAK, "hello", tmp_path / "said.wav", "en_GB")

    assert command[1:3] == ["-v", "en"]


def test_an_agent_that_declared_no_language_is_called_in_spanish(tmp_path: Path) -> None:
    command = speech.the_command(speech.ESPEAK, "hola", tmp_path / "said.wav", None)

    assert command[1:3] == ["-v", "es"]


def test_a_language_say_has_nobody_for_is_read_by_the_machines_own_voice(tmp_path: Path) -> None:
    command = speech.the_command(speech.SAY, "bonjour", tmp_path / "said.wav", "fr")

    assert "-v" not in command
