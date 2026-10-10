"""Tests for what a class declares: the field read, fixed, and taken back when sent empty."""

import pytest

from pinecall.domain.agent import AgentConfig, Docs, Greeting, MemoryPolicy, Model, Voice
from pinecall.domain.errors import DeclarationRefused
from pinecall.session._environment import environment_of
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import (
    DocsConfig,
    GreetingConfig,
    MemoryConfig,
    ModelConfig,
    Pronunciation,
    VoiceConfig,
)

NOTHING = AgentConfig(slug="clinica-norte")


def test_each_field_the_class_sends_becomes_the_configs_and_is_fixed() -> None:
    declared = Declared(
        language="es",
        voice=VoiceConfig(provider="cartesia", model="sonic-2", voice_id="v-1"),
        llm=ModelConfig(
            provider="openai",
            model="gpt-5.4-mini",
            builds="responses.LLM",
            options={"use_websocket": True},
        ),
        greeting=GreetingConfig(say="Hola."),
        says=[Pronunciation(word="GSA", spoken="G S A")],
        docs=DocsConfig(base="clinica", k=4),
        memory=MemoryConfig(remember=["allergies"]),
    )
    changed = environment_of(NOTHING, declared)
    assert changed["voice"] == Voice("cartesia", "sonic-2", "v-1")
    assert changed["llm"] == Model(
        "openai", "gpt-5.4-mini", builds="responses.LLM", options={"use_websocket": True}
    )
    assert changed["greeting"] == Greeting(say="Hola.")
    assert changed["says"] == {"GSA": "G S A"}
    assert changed["bases"] == (Docs(base="clinica", k=4),)
    assert changed["memory"] == MemoryPolicy(remember=("allergies",))
    assert changed["fixed"] == frozenset(
        {"language", "voice", "llm", "greeting", "says", "docs", "memory"}
    )


def test_a_field_sent_empty_goes_back_to_the_settings_and_one_left_out_stays_fixed() -> None:
    current = AgentConfig(slug="clinica-norte", fixed=frozenset({"voice", "llm"}))
    changed = environment_of(current, Declared(voice=None, tools=[]))
    assert changed == {"fixed": frozenset({"llm"})}


def test_a_voice_the_class_declares_names_its_vendor_and_the_voice() -> None:
    with pytest.raises(DeclarationRefused, match=r"@voice\('<vendor>', '<voice>'\)"):
        environment_of(NOTHING, Declared(voice=VoiceConfig(voice_id="v-1")))
    with pytest.raises(DeclarationRefused, match="names its vendor and the voice"):
        environment_of(NOTHING, Declared(voice=VoiceConfig(provider="cartesia")))


def test_a_vendor_not_installed_or_not_doing_the_stage_is_refused_when_declared() -> None:
    with pytest.raises(DeclarationRefused, match="no vendor named 'openai-but-misspelt'"):
        environment_of(
            NOTHING, Declared(llm=ModelConfig(provider="openai-but-misspelt", model="x"))
        )
    with pytest.raises(DeclarationRefused, match="anthropic has no stt"):
        environment_of(NOTHING, Declared(stt=ModelConfig(provider="anthropic", model="x")))


def test_who_ends_the_turn_is_the_ears_and_refused_on_the_model() -> None:
    ears = ModelConfig(provider="deepgram", model="flux-general-multi", end_of_turn="smart-turn")
    assert environment_of(NOTHING, Declared(stt=ears))["stt"] == Model(
        "deepgram", "flux-general-multi", end_of_turn="smart-turn"
    )
    thinking = ModelConfig(provider="openai", model="gpt-5.4-mini", end_of_turn="stt")
    with pytest.raises(DeclarationRefused, match="end_of_turn is the ears'"):
        environment_of(NOTHING, Declared(llm=thinking))


def test_the_judge_the_class_declares_is_a_model_and_is_fixed() -> None:
    local = ModelConfig(provider="openai", model="qwen3-32b", options={"base_url": "http://gpu/v1"})
    changed = environment_of(NOTHING, Declared(judge=local))
    assert changed["judge"] == Model("openai", "qwen3-32b", options={"base_url": "http://gpu/v1"})
    assert changed["fixed"] == frozenset({"judge"})
    with pytest.raises(DeclarationRefused, match="deepgram has no llm"):
        environment_of(NOTHING, Declared(judge=ModelConfig(provider="deepgram", model="x")))
