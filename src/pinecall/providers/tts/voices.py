"""The framework's curated voices: the name a tenant writes, and the id the vendor needs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pinecall.types import DeclarationRefused, Voice

# A voice id as ElevenLabs writes one: twenty letters and digits, and nothing a person would type
# as a name. It is how a tenant who has their own voice writes it without asking us for a row.
A_VENDOR_ID = re.compile(r"^[A-Za-z0-9]{20}$")


@dataclass(frozen=True)
class CuratedVoice:
    """One voice this build speaks in: whose it is, its id there, the language it was chosen for."""

    vendor: str
    voice_id: str
    language: str


# Every id here is a `premade` ElevenLabs voice, checked with GET /v1/voices/<id>. A `professional`
# or a cloned voice exists only inside the workspace that added it, so putting one here would ship
# every other self-hoster the same 1008 this table was written to end. See decisions/providers.md.
VOICES: dict[str, CuratedVoice] = {
    "carolina": CuratedVoice("elevenlabs", "EXAVITQu4vr4xnSDxMaL", "es"),
    "mateo": CuratedVoice("elevenlabs", "cjVigY5qzO86Huf0OWal", "es"),
    "charlie": CuratedVoice("elevenlabs", "IKne3meq5aSn9XLyUdCD", "en"),
}


def voice_names() -> tuple[str, ...]:
    """Every name this build curates, in the order a refusal lists and a console offers them."""
    return tuple(sorted(VOICES))


def known_voices() -> str:
    """Every voice this build can be asked for by name, as a refusal lists them."""
    return ", ".join(voice_names())


# The one door between what a tenant wrote and what a vendor is sent. It runs when the app declares
# itself, never when the agent first speaks: a name nobody curated is a build that does not start.
def voice_declared(asked: str | None, vendor: str | None, voice_id: str | None) -> Voice:
    """A declared voice as the vendor needs it, or a refusal naming the voices this build knows."""
    if voice_id:
        return Voice(provider=vendor or "", voice_id=voice_id)
    if not asked:
        raise DeclarationRefused(
            f"a voice is a curated name or a vendor id; this build knows: {known_voices()}"
        )
    if curated := VOICES.get(asked):
        return Voice(provider=vendor or curated.vendor, voice_id=curated.voice_id)
    if A_VENDOR_ID.match(asked):
        return Voice(provider=vendor or "", voice_id=asked)
    raise DeclarationRefused(f"no voice named {asked!r}; this build knows: {known_voices()}")


def language_of(voice: Voice) -> str | None:
    """The language a curated voice was chosen for, for an agent that declared none of its own."""
    for curated in VOICES.values():
        if curated.vendor == voice.provider and curated.voice_id == voice.voice_id:
            return curated.language
    return None
