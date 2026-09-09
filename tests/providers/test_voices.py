"""The word a tenant writes for a voice, and the id the vendor is sent: one table, one door."""

import pytest

from pinecall.providers.declaration import configured
from pinecall.providers.tts.voices import VOICES, voice_declared
from pinecall.types import AgentConfig, DeclarationRefused, Voice
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

# Twenty letters and digits: what a tenant with their own ElevenLabs voice writes instead of a name.
THEIR_OWN = "Xb7hH8MSUJpSbSDYk0k2"


def declaring(voice: defs.VoiceConfig) -> Voice | None:
    """One agent declaring a voice and nothing else, as the app's socket takes it."""
    return configured(AgentConfig(slug="clinica-norte"), defs.AgentConfig(voice=voice)).voice


def test_a_curated_name_reaches_the_vendor_as_the_id_the_vendor_knows() -> None:
    """`voice = "carolina"` is a name here and an id there: the table is the whole difference."""
    spoken = voice_declared("carolina", None, None)
    assert spoken == Voice(provider="elevenlabs", voice_id=VOICES["carolina"].voice_id)
    assert spoken.voice_id != "carolina"


def test_a_tenants_own_vendor_id_passes_through_untouched() -> None:
    """A voice we never curated is still theirs to use, written as the vendor's own id."""
    assert voice_declared(THEIR_OWN, None, None).voice_id == THEIR_OWN
    assert voice_declared(None, "elevenlabs", THEIR_OWN) == Voice(
        provider="elevenlabs", voice_id=THEIR_OWN
    )


def test_a_voice_nobody_curated_is_refused_when_the_app_declares_itself() -> None:
    """The refusal the first real call did not get: at the declaration, naming what it could say."""
    with pytest.raises(DeclarationRefused) as refusal:
        declaring(defs.VoiceConfig(name="carolinaa"))
    assert "no voice named 'carolinaa'" in str(refusal.value)
    for known in VOICES:
        assert known in str(refusal.value)


def test_the_declaration_the_gateway_keeps_carries_an_id_and_never_a_name() -> None:
    """What the worker asks the gateway for is resolved; nothing downstream ever sees the name."""
    kept = declaring(defs.VoiceConfig(name="carolina", model="eleven_flash_v2_5"))
    assert kept == Voice(
        provider="elevenlabs", model="eleven_flash_v2_5", voice_id=VOICES["carolina"].voice_id
    )
