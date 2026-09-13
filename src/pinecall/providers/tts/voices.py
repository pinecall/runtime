"""The framework's curated voices: the name a tenant writes, and the id the vendor needs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pinecall.providers.catalog import canonical
from pinecall.types import DeclarationRefused, Voice

# A voice id as ElevenLabs writes one: twenty letters and digits, and nothing a person would type
# as a name. It is how a tenant on that vendor writes their own voice without asking us for a row,
# and it is ElevenLabs' shape and nobody else's — Cartesia writes a uuid, Rime a word, Hume a
# sentence — so it is only ever consulted for ElevenLabs. See WE_KNOW_THE_SHAPE below.
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
# The language is the VOICE's, asked of the vendor and not chosen here. Two of these said `es`
# and were an American reading Spanish: `carolina` is ElevenLabs' Sarah (accent `american`,
# language `en`) and `mateo` is Eric (the same), which is what a caller hears as a foreigner
# pronouncing their language. Every one of ElevenLabs' 21 premade voices is English — there is no
# Spanish premade to put here — so a Spanish-speaking agent declares a voice id of its own, from a
# workspace that has one, and `language_of` stops promising what these cannot deliver.
VOICES: dict[str, CuratedVoice] = {
    "carolina": CuratedVoice("elevenlabs", "EXAVITQu4vr4xnSDxMaL", "en"),
    "mateo": CuratedVoice("elevenlabs", "cjVigY5qzO86Huf0OWal", "en"),
    "charlie": CuratedVoice("elevenlabs", "IKne3meq5aSn9XLyUdCD", "en"),
}

# The one vendor whose id shape this file knows — and it is also the vendor a declaration that
# named none will speak with (providers/pipeline.py, DEFAULT_TTS). Because the shape is known, a
# word there that is neither a curated name nor an id can be refused as the typo it is, which is
# what this table was written for: `carolina` reached ElevenLabs as a voice_id and came back 1008
# seven times in one call. Nobody else's shape is known here, so nobody else's word is judged.
WE_KNOW_THE_SHAPE = "elevenlabs"

# What a declaration that named no vendor is refused with. Both ways out are in the sentence,
# because a tenant on Cartesia reading only the first half would go looking for a curated name
# that is never going to exist for their vendor.
NO_VOICE = (
    "a voice is a curated name, a vendor id, or a provider and that provider's own id; "
    "this build curates: {known}"
)
NO_SUCH_VOICE = (
    "no voice named {asked!r}; this build curates {known} — or name the provider beside it and "
    "the word is taken as that provider's own id"
)
NOTHING_TO_SPEAK_WITH = "the {vendor} voice has no name and no id: one of the two has to be there"


def voice_names() -> tuple[str, ...]:
    """Every name this build curates, in the order a refusal lists and a console offers them."""
    return tuple(sorted(VOICES))


def known_voices() -> str:
    """Every voice this build can be asked for by name, as a refusal lists them."""
    return ", ".join(voice_names())


# The one door between what a tenant wrote and what a vendor is sent. It runs when the app declares
# itself, never when the agent first speaks: a name nobody curated is a build that does not start.
#
# The rule turns on ONE question — WHICH vendor will speak? For the one whose ids this file can
# recognise, a word that is neither a curated name nor an id of that shape is a typo, and a typo
# is refused here rather than heard as silence on a call. For every other vendor the word is that
# vendor's own id and we are not the ones who know its shape: the vendor refuses an id it does not
# have, and it refuses it while the app is declaring itself, not at the first utterance.
def voice_declared(asked: str | None, vendor: str | None, voice_id: str | None) -> Voice:
    """A declared voice as the vendor needs it, or a refusal naming the voices this build knows."""
    named = canonical(vendor) if vendor else ""
    if voice_id:
        return Voice(provider=named, voice_id=voice_id)
    if not asked:
        raise DeclarationRefused(
            NOTHING_TO_SPEAK_WITH.format(vendor=named)
            if named
            else NO_VOICE.format(known=known_voices())
        )
    # A curated name still resolves when it belongs to the vendor that was named — `voice:
    # "carolina", provider: "elevenlabs"` is the same voice written twice, and refusing it would
    # be pedantry. A curated name against ANOTHER vendor is that vendor's own word, not ours.
    curated = VOICES.get(asked)
    if curated is not None and named in ("", curated.vendor):
        return Voice(provider=named or curated.vendor, voice_id=curated.voice_id)
    _refuse_a_typo(asked, named or WE_KNOW_THE_SHAPE)
    return Voice(provider=named, voice_id=asked)


def _refuse_a_typo(asked: str, speaking: str) -> None:
    """Judge the word only for the one vendor whose ids this file can tell from a mistake."""
    if speaking == WE_KNOW_THE_SHAPE and not A_VENDOR_ID.match(asked):
        raise DeclarationRefused(NO_SUCH_VOICE.format(asked=asked, known=known_voices()))


def language_of(voice: Voice) -> str | None:
    """The language a curated voice was chosen for, for an agent that declared none of its own."""
    for curated in VOICES.values():
        if curated.vendor == voice.provider and curated.voice_id == voice.voice_id:
            return curated.language
    return None
