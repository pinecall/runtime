"""The world's settings on what the class declared: every knob's rule, and the org's words."""

import re

import pytest

from pinecall.providers.tts.elevenlabs import DEFAULT_MODEL
from pinecall.providers.tts.voices import VOICES
from pinecall.providers.tuning import NOT_RUN_HERE, the_llm, the_voice, tuned
from pinecall.types import (
    AgentConfig,
    DeclarationRefused,
    Docs,
    Greeting,
    Hangup,
    Lexicon,
    MemoryPolicy,
    Tuning,
    Turn,
)

pytestmark = pytest.mark.unit

DECLARED = AgentConfig(slug="clinica-norte", language="es")
NOTHING = Lexicon()
A_UUID = "de38f545-c574-44e8-9b54-a7d6fec1c6b1"


def test_nothing_set_is_the_declaration_with_every_knob_at_the_runtimes_default() -> None:
    config = tuned(DECLARED, Tuning(), NOTHING)
    assert (config.slug, config.language) == ("clinica-norte", "es")
    assert (config.voice, config.stt, config.llm, config.greeting, config.knowledge) == (None,) * 5
    assert config.bases == () and config.says == {} and config.hears == ()


def test_a_model_knob_reads_three_ways() -> None:
    """`vendor/model` names both; a vendor alone keeps its model; a model alone, the default."""
    both = tuned(DECLARED, Tuning(llm="openai/gpt-5"), NOTHING).llm
    vendor = tuned(DECLARED, Tuning(llm="openai"), NOTHING).llm
    model = tuned(DECLARED, Tuning(llm="claude-sonnet-4-5"), NOTHING).llm
    assert both is not None and (both.provider, both.model) == ("openai", "gpt-5")
    assert vendor is not None and (vendor.provider, vendor.model) == ("openai", "")
    assert model is not None and (model.provider, model.model) == ("anthropic", "claude-sonnet-4-5")


def test_a_vendor_this_build_has_no_file_for_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="no llm vendor named"):
        tuned(DECLARED, Tuning(llm="openai-but-misspelt/gpt-5"), NOTHING)


def test_a_voice_name_becomes_the_id_the_vendor_knows_and_tts_moves_the_stage() -> None:
    named = tuned(DECLARED, Tuning(voice="mateo"), NOTHING).voice
    assert named is not None and named.voice_id == VOICES["mateo"].voice_id
    assert named.provider == "elevenlabs"
    moved = tuned(DECLARED, Tuning(tts="cartesia/sonic-3", voice=A_UUID), NOTHING).voice
    assert moved is not None
    assert (moved.provider, moved.model, moved.voice_id) == ("cartesia", "sonic-3", A_UUID)
    with pytest.raises(DeclarationRefused, match="no voice named 'a-uuid'"):
        tuned(DECLARED, Tuning(tts="cartesia", voice="a-uuid"), NOTHING)


def test_a_model_the_vendor_file_does_not_vouch_for_is_refused() -> None:
    """`sonic-9` on Cartesia would die at the vendor on the first line; it is refused when set."""
    with pytest.raises(
        DeclarationRefused, match="no cartesia model 'sonic-9'; this build has sonic-3, sonic-2"
    ):
        tuned(DECLARED, Tuning(tts="cartesia", tts_model="sonic-9"), NOTHING)
    kept = tuned(DECLARED, Tuning(tts="cartesia/sonic-2"), NOTHING).voice
    assert kept is not None and kept.model == "sonic-2"


def test_an_elevenlabs_model_this_build_does_not_run_is_refused() -> None:
    refused = NOT_RUN_HERE.format(asked="eleven_turbo_v2_5", instead=DEFAULT_MODEL)
    with pytest.raises(DeclarationRefused, match=re.escape(refused)):
        tuned(DECLARED, Tuning(tts="elevenlabs", tts_model="eleven_turbo_v2_5"), NOTHING)


def test_the_opening_is_the_worlds_words_or_nobodys() -> None:
    said = Greeting(say="Buenas.", allow_interruptions=True)
    assert tuned(DECLARED, Tuning(greeting=said), NOTHING).greeting == said
    assert tuned(DECLARED, Tuning(), NOTHING).greeting is None


def test_hangup_turn_memory_knowledge_and_the_bases_are_the_worlds() -> None:
    tuning = Tuning(
        hangup=Hangup(when="the caller says bye"),
        turn=Turn(endpointing_ms=300),
        memory=MemoryPolicy(remember=("allergies",)),
        knowledge="# Clínica Norte\n\nAbrimos a las nueve.",
        bases=(Docs(base="clinica", k=4), Docs(base="precios")),
    )
    config = tuned(DECLARED, tuning, NOTHING)
    assert config.hangup == Hangup(when="the caller says bye")
    assert config.turn == Turn(endpointing_ms=300)
    assert config.memory == MemoryPolicy(remember=("allergies",))
    assert config.knowledge == "# Clínica Norte\n\nAbrimos a las nueve."
    assert [docs.base for docs in config.bases] == ["clinica", "precios"]


def test_the_lexicon_is_the_agents_says_and_hears() -> None:
    words = Lexicon(said={"Vidal": "vidál", "GSA": "G S A"}, heard=("GSA", "Vidal"))
    config = tuned(DECLARED, Tuning(), words)
    assert config.says == {"Vidal": "vidál", "GSA": "G S A"}
    assert config.hears == ("GSA", "Vidal")


# The same parser reads a synthetic caller's three knobs (api/personas.py, api/evals/voice.py),
# so a persona's `llm` and `voice` mean exactly what the agent's do, and unset is unset.
def test_a_persona_names_its_model_and_its_voice_in_the_agents_own_words() -> None:
    model = the_llm("openai/gpt-5")
    assert model is not None and (model.provider, model.model) == ("openai", "gpt-5")
    voice = the_voice(None, "mateo")
    assert voice is not None and (voice.provider, voice.voice_id) == (
        "elevenlabs",
        VOICES["mateo"].voice_id,
    )
    assert (the_llm(None), the_voice(None, None)) == (None, None)


def test_a_voice_call_runs_ten_minutes_unless_the_world_says_otherwise() -> None:
    """Unset is the runtime's ten minutes; a set limit wins, and 0 — no limit — is a setting too."""
    assert tuned(DECLARED, Tuning(), NOTHING).max_duration_s == 600
    assert tuned(DECLARED, Tuning(max_duration_s=900), NOTHING).max_duration_s == 900
    assert tuned(DECLARED, Tuning(max_duration_s=0), NOTHING).max_duration_s == 0
