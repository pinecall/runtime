"""A simulated caller's own voice, at a vendor through livekit's plugin, as PCM at a room's rate."""

from __future__ import annotations

from dataclasses import dataclass

from livekit import rtc

from pinecall._settings import Settings
from pinecall.providers import tts
from pinecall.providers.language import primary
from pinecall.providers.registry import Asked, Speech
from pinecall.providers.tts import DEFAULT_TTS
from pinecall.types import NOTHING_BROUGHT, Brought
from pinecall.types import Voice as DeclaredVoice

# The rate everything downstream is written for: LiveKit's own examples publish at 48 kHz mono.
# Every line comes back at this rate, whatever the vendor sent, so a caller's track can be opened
# before a word of it exists.
SAMPLE_RATE = 48_000
CHANNELS = 1

# The caller is a person, so it speaks with a person's voice: the box's speech tool (`say`,
# espeak-ng) was a robot the agent's ears misheard — "Dana Ruiz" came back as "Caroline" and then
# "saying release" (2026-09-19). The same vendor the agent speaks with by default, in a voice that
# is NOT one an agent is given (providers/tts/cartesia.py, VOICE_FOR), and a native speaker of the
# call's language: two per language, a man and a woman, the second for the agent that speaks in the
# first, so the two sides of a call are never one voice. A language with no pair of its own is
# called in English's. These are what a caller that declared no voice speaks in
# (`Speaking.declared`). ElevenLabs played them until it stopped answering (2026-09-25).
VENDOR = DEFAULT_TTS
CALLER_VOICES: dict[str, tuple[str, str]] = {
    "es": (
        "13ff5deb-2591-42ad-a356-63a04e524411",  # Marcos - Steady Advisor: Spain
        "538a8872-3799-4df5-b373-b78493b766c6",  # Blanca - Graceful Host: Spain
    ),
    "en": (
        "87286a8d-7ea7-4235-a41a-dd9fa6630feb",  # Henry - Plainspoken Guy: American
        "e8e5fffb-252c-436d-b842-8879b84445b6",  # Cathy - Coworker: American
    ),
}

# What the sports bulletin of those calls was: a second voice, reading something nobody is listening
# to. Spoken by the same vendor, in a third voice nobody else on the line has.
A_TELEVISION = (
    "A continuación, el resumen deportivo de la jornada. El equipo local venció por dos goles a "
    "uno en un partido disputado hasta el último minuto, y el entrenador destacó el esfuerzo de "
    "sus jugadores en la rueda de prensa posterior al encuentro."
)
A_TELEVISION_VOICE = "b5aa8098-49ef-475d-89b0-c9262ecf33fd"  # Luis - News Caster: Spain
A_TELEVISION_LANGUAGE = "es"


@dataclass(frozen=True)
class Speaking:
    """How the caller's lines are read: in the agent's language, not in its voice, on whose key."""

    language: str | None = None
    # The id the agent itself speaks with, when it is known: the caller takes another one.
    agents_voice: str | None = None
    # The org's own keys when it brought any, and what the box lends it for the rest.
    brought: Brought = NOTHING_BROUGHT
    # The voice the persona declared for itself — its `tts` and `voice`, read by the agent's own
    # parser (providers/tuned_declaration.py:the_voice) — when it declared one. Then that vendor,
    # that model and that id speak, whatever the agent speaks in; None is a premade the agent does
    # not have.
    declared: DeclaredVoice | None = None


def a_callers_voice(agents_voice: str | None, language: str | None = None) -> str:
    """The first caller voice of the call's language that is not the agent's."""
    pair = CALLER_VOICES.get(primary(language) or "", CALLER_VOICES["en"])
    return next(voice for voice in pair if voice != agents_voice)


class Voice:
    """One voice at the vendor, built once for a call and asked for every line of it.

    The plugin reaches the vendor through livekit's http session, which only a job binds: the
    gateway holds the call outside one, so whoever builds this does it inside
    `livekit.agents.utils.http_context.open()` (calling.py), livekit's own answer for that.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        voice_id: str | None,
        language: str | None,
        brought: Brought,
        vendor: str = VENDOR,
        model: str | None = None,
    ) -> None:
        self._speech: Speech = tts.VENDORS.build(
            vendor,
            Asked(
                settings=settings,
                model=model,
                voice_id=voice_id,
                language=primary(language),
                keys=brought.keys,
                lends=brought.lends,
            ),
        )

    @classmethod
    def of_the_caller(cls, settings: Settings, speaking: Speaking) -> Voice:
        """The caller's voice: the one it declared, else a premade the agent does not have."""
        if speaking.declared is not None:
            return cls(
                settings,
                vendor=speaking.declared.provider,
                model=speaking.declared.model,
                voice_id=speaking.declared.voice_id,
                language=speaking.language,
                brought=speaking.brought,
            )
        return cls(
            settings,
            voice_id=a_callers_voice(speaking.agents_voice, speaking.language),
            language=speaking.language,
            brought=speaking.brought,
        )

    @classmethod
    def of_a_television(cls, settings: Settings, brought: Brought) -> Voice:
        """The interferer's voice: a presenter nobody on the call sounds like."""
        return cls(
            settings, voice_id=A_TELEVISION_VOICE, language=A_TELEVISION_LANGUAGE, brought=brought
        )

    async def spoken(self, text: str) -> bytes:
        """One line said out loud, as 16-bit mono PCM at SAMPLE_RATE."""
        frame = await self._speech.synthesize(text).collect()
        return at_the_rooms_rate(bytes(frame.data), frame.sample_rate)

    async def aclose(self) -> None:
        """Let the plugin go: its sockets are the call's, not the process's."""
        await self._speech.aclose()


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
