"""A simulated caller's own voice: ElevenLabs through livekit's plugin, as PCM at a room's rate."""

from __future__ import annotations

from dataclasses import dataclass

from livekit import rtc

from pinecall._settings import Settings
from pinecall.providers import tts
from pinecall.providers.registry import Asked, Speech
from pinecall.types import NO_ORG_KEYS, ProviderKeys

# The rate everything downstream is written for: LiveKit's own examples publish at 48 kHz mono.
# Every line comes back at this rate, whatever the vendor sent, so a caller's track can be opened
# before a word of it exists.
SAMPLE_RATE = 48_000
CHANNELS = 1

# The caller is a person, so it speaks with a person's voice: the box's speech tool (`say`,
# espeak-ng) was a robot the agent's ears misheard — "Dana Ruiz" came back as "Caroline" and then
# "saying release" (2026-09-19, maravilla). The same vendor the agent speaks with, through the same
# vendor file, in a voice that is NOT one an agent is given: two premade ElevenLabs voices, checked
# with GET /v1/voices/<id>, none of them curated in providers/tts/voices.py. The second is for the
# agent that speaks in the first, so the two sides of a call are never one voice.
VENDOR = "elevenlabs"
CALLER_VOICES = (
    "nPczCjzI2devNBz1zQrb",  # Brian: male, American
    "cgSgspJ2msm6clMCkdW9",  # Jessica: female, American
)

# What the sports bulletin of those calls was: a second voice, reading something nobody is listening
# to. Spoken by the same vendor, in a third voice nobody else on the line has.
A_TELEVISION = (
    "A continuación, el resumen deportivo de la jornada. El equipo local venció por dos goles a "
    "uno en un partido disputado hasta el último minuto, y el entrenador destacó el esfuerzo de "
    "sus jugadores en la rueda de prensa posterior al encuentro."
)
A_TELEVISION_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # George: male, British
A_TELEVISION_LANGUAGE = "es"


@dataclass(frozen=True)
class Speaking:
    """How the caller's lines are read: in the agent's language, not in its voice, on whose key."""

    language: str | None = None
    # The id the agent itself speaks with, when it is known: the caller takes another one.
    agents_voice: str | None = None
    # The org's own keys when it brought any; empty is the box's.
    keys: ProviderKeys = NO_ORG_KEYS


def a_callers_voice(agents_voice: str | None) -> str:
    """The first caller voice that is not the agent's."""
    return next(voice for voice in CALLER_VOICES if voice != agents_voice)


class Voice:
    """One voice at the vendor, built once for a call and asked for every line of it.

    The plugin reaches the vendor through livekit's http session, which only a job binds: the
    gateway holds the call outside one, so whoever builds this does it inside
    `livekit.agents.utils.http_context.open()` (calling.py), livekit's own answer for that.
    """

    def __init__(
        self, settings: Settings, *, voice_id: str, language: str | None, keys: ProviderKeys
    ) -> None:
        self._speech: Speech = tts.VENDORS.build(
            VENDOR,
            Asked(settings=settings, voice_id=voice_id, language=_primary(language), keys=keys),
        )

    @classmethod
    def of_the_caller(cls, settings: Settings, speaking: Speaking) -> Voice:
        """The caller's voice: the agent's language, a voice the agent does not have."""
        return cls(
            settings,
            voice_id=a_callers_voice(speaking.agents_voice),
            language=speaking.language,
            keys=speaking.keys,
        )

    @classmethod
    def of_a_television(cls, settings: Settings, keys: ProviderKeys) -> Voice:
        """The interferer's voice: a presenter nobody on the call sounds like."""
        return cls(settings, voice_id=A_TELEVISION_VOICE, language=A_TELEVISION_LANGUAGE, keys=keys)

    async def spoken(self, text: str) -> bytes:
        """One line said out loud, as 16-bit mono PCM at SAMPLE_RATE."""
        frame = await self._speech.synthesize(text).collect()
        return at_the_rooms_rate(bytes(frame.data), frame.sample_rate)

    async def aclose(self) -> None:
        """Let the plugin go: its sockets are the call's, not the process's."""
        await self._speech.aclose()


def _primary(language: str | None) -> str | None:
    """`es-ES`, `en_US` and `en` are one language to the vendor: the primary subtag is sent."""
    return None if not language else language.replace("_", "-").split("-")[0].lower()


# The vendor answers at its own rate (ElevenLabs: 22 050 or 24 000 Hz), which is brought to the
# room's by livekit's own resampler.
def at_the_rooms_rate(pcm: bytes, rate: int) -> bytes:
    """The same samples at SAMPLE_RATE; untouched when they already are."""
    if rate == SAMPLE_RATE:
        return pcm
    resampler = rtc.AudioResampler(rate, SAMPLE_RATE, num_channels=CHANNELS)
    frame = rtc.AudioFrame(
        data=pcm, sample_rate=rate, num_channels=CHANNELS, samples_per_channel=len(pcm) // 2
    )
    resampled = [*resampler.push(frame), *resampler.flush()]
    return b"".join(bytes(one.data) for one in resampled)
