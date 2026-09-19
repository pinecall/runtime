"""What the org set laid over what the app declared: every knob's rule, and the lexicon merged."""

import re

import pytest

from pinecall.providers.tts.elevenlabs import DEFAULT_MODEL
from pinecall.providers.tts.voices import VOICES
from pinecall.providers.tuning import NOT_RUN_HERE, tuned
from pinecall.types import (
    AgentConfig,
    DeclarationRefused,
    Docs,
    Greeting,
    Hangup,
    Lexicon,
    MemoryPolicy,
    Model,
    Tuning,
    Turn,
    Voice,
)

pytestmark = pytest.mark.unit

DECLARED = AgentConfig(
    slug="clinica-norte",
    language="es",
    greeting=Greeting(say="Clínica Norte, buenas.", allow_interruptions=False),
    voice=Voice(provider="elevenlabs", voice_id="a-declared-voice"),
    llm=Model(provider="anthropic", model="claude-haiku-4-5"),
    says={"Vidal": "bidál"},
    hears=("Vidal",),
)
NOTHING = Lexicon()


def test_nothing_set_leaves_the_declaration_exactly_as_it_was() -> None:
    assert tuned(DECLARED, Tuning(), NOTHING) == DECLARED


def test_a_model_knob_reads_three_ways() -> None:
    """`vendor/model` names both; a vendor alone keeps its model; a model alone keeps the vendor."""
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
    moved = tuned(DECLARED, Tuning(tts="cartesia/sonic-3", voice="a-uuid"), NOTHING).voice
    assert moved is not None
    assert (moved.provider, moved.model, moved.voice_id) == ("cartesia", "sonic-3", "a-uuid")


def test_an_elevenlabs_model_this_build_does_not_run_is_refused() -> None:
    refused = NOT_RUN_HERE.format(asked="eleven_turbo_v2_5", instead=DEFAULT_MODEL)
    with pytest.raises(DeclarationRefused, match=re.escape(refused)):
        tuned(DECLARED, Tuning(tts_model="eleven_turbo_v2_5"), NOTHING)


def test_the_openings_words_are_set_and_its_interruptibility_kept_unless_said() -> None:
    words = tuned(DECLARED, Tuning(greeting=Greeting(say="Buenas.")), NOTHING).greeting
    assert words == Greeting(say="Buenas.", allow_interruptions=False)
    said = Greeting(say="Buenas.", allow_interruptions=True)
    assert tuned(DECLARED, Tuning(greeting=said), NOTHING).greeting == said


def test_hangup_turn_memory_and_the_first_base_are_the_orgs_when_it_set_them() -> None:
    tuning = Tuning(
        hangup=Hangup(when="the caller says bye"),
        turn=Turn(endpointing_ms=300),
        memory=MemoryPolicy(remember=("allergies",)),
        knowledge=(Docs(base="clinica", k=4), Docs(base="precios")),
    )
    config = tuned(DECLARED, tuning, NOTHING)
    assert config.hangup == Hangup(when="the caller says bye")
    assert config.turn == Turn(endpointing_ms=300)
    assert config.memory == MemoryPolicy(remember=("allergies",))
    assert config.docs == Docs(base="clinica", k=4)


def test_the_lexicon_is_merged_into_says_and_hears_and_the_orgs_word_wins() -> None:
    words = Lexicon(said={"Vidal": "vidál", "GSA": "G S A"}, heard=("GSA", "Vidal"))
    config = tuned(DECLARED, Tuning(), words)
    assert config.says == {"Vidal": "vidál", "GSA": "G S A"}
    assert config.hears == ("Vidal", "GSA")
