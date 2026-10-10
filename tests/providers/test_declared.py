"""The org's settings over an agent's declaration, and every model knob read the same way."""

import pytest

from pinecall.domain.agent import (
    AgentConfig,
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
from pinecall.domain.errors import DeclarationRefused
from pinecall.providers.catalog import Providers
from pinecall.providers.declared import apply_tuning, model_of

DECLARED = AgentConfig(slug="clinica-norte", language="es")
NOTHING = Lexicon()


def test_nothing_set_is_the_declaration_with_every_knob_at_the_runtimes_default(
    configured: Providers,
) -> None:
    config = apply_tuning(DECLARED, Tuning(), NOTHING, defaults=configured.defaults)
    assert (config.slug, config.language) == ("clinica-norte", "es")
    assert (config.voice, config.stt, config.llm, config.greeting, config.knowledge) == (None,) * 5
    assert config.bases == ()
    assert config.says == {}
    assert config.hears == ()


def test_a_model_knob_reads_three_ways(configured: Providers) -> None:
    def llm_of(text: str) -> Model | None:
        return apply_tuning(DECLARED, Tuning(llm=text), NOTHING, defaults=configured.defaults).llm

    assert llm_of("openai/gpt-5") == Model(provider="openai", model="gpt-5")
    assert llm_of("openai") == Model(provider="openai", model="")
    assert llm_of("claude-sonnet-5") == Model(provider="anthropic", model="claude-sonnet-5")


def test_a_bare_model_lands_on_the_vendor_the_agent_already_runs(configured: Providers) -> None:
    declared = AgentConfig(slug="clinica-norte", llm=Model(provider="groq", model="llama-3"))
    tuned = apply_tuning(declared, Tuning(llm="llama-4"), NOTHING, defaults=configured.defaults)
    assert tuned.llm == Model(provider="groq", model="llama-4")


def test_a_model_id_keeps_every_slash_after_the_vendors() -> None:
    assert model_of("livekit/openai/gpt-5-mini", "llm", in_use="anthropic") == Model(
        "livekit", "openai/gpt-5-mini"
    )


def test_a_vendor_that_is_not_installed_is_refused_naming_where_the_list_is() -> None:
    with pytest.raises(DeclarationRefused, match="no vendor named 'openai-but-misspelt'"):
        model_of("openai-but-misspelt/gpt-5", "llm", in_use="anthropic")


def test_a_vendor_that_does_not_do_the_stage_is_refused_with_what_it_does() -> None:
    with pytest.raises(DeclarationRefused, match="anthropic has no stt: it does llm"):
        model_of("anthropic", "stt", in_use="deepgram")


def test_tts_moves_the_stage_and_voice_is_that_vendors_own_id(configured: Providers) -> None:
    tuning = Tuning(tts="elevenlabs/eleven_flash_v2_5", voice="EXAVITQu4vr4xnSDxMaL")
    voice = apply_tuning(DECLARED, tuning, NOTHING, defaults=configured.defaults).voice
    assert voice == Voice("elevenlabs", "eleven_flash_v2_5", "EXAVITQu4vr4xnSDxMaL")


def test_a_voice_alone_is_an_id_of_the_vendor_in_use_and_never_checked(
    configured: Providers,
) -> None:
    voice = apply_tuning(
        DECLARED, Tuning(voice="whatever-id"), NOTHING, defaults=configured.defaults
    ).voice
    assert voice == Voice("cartesia", None, "whatever-id")


def test_the_model_named_apart_is_the_model_of_the_voice_in_use(configured: Providers) -> None:
    tuning = Tuning(tts_model="sonic-2")
    voice = apply_tuning(DECLARED, tuning, NOTHING, defaults=configured.defaults).voice
    assert voice == Voice("cartesia", "sonic-2", None)


def test_the_opening_is_the_worlds_words_or_nobodys(configured: Providers) -> None:
    text = Greeting(say="Buenas.", allow_interruptions=True)
    tuned = apply_tuning(DECLARED, Tuning(greeting=text), NOTHING, defaults=configured.defaults)
    assert tuned.greeting == text
    assert apply_tuning(DECLARED, Tuning(), NOTHING, defaults=configured.defaults).greeting is None


def test_hangup_turn_memory_knowledge_and_the_bases_are_the_worlds(configured: Providers) -> None:
    tuning = Tuning(
        hangup=Hangup(when="the caller says bye"),
        turn=Turn(endpointing_ms=300),
        memory=MemoryPolicy(remember=("allergies",)),
        knowledge="# Clínica Norte\n\nAbrimos a las nueve.",
        bases=(Docs(base="clinica", k=4), Docs(base="precios")),
    )
    config = apply_tuning(DECLARED, tuning, NOTHING, defaults=configured.defaults)
    assert config.hangup == Hangup(when="the caller says bye")
    assert config.turn == Turn(endpointing_ms=300)
    assert config.memory == MemoryPolicy(remember=("allergies",))
    assert config.knowledge == "# Clínica Norte\n\nAbrimos a las nueve."
    assert [docs.base for docs in config.bases] == ["clinica", "precios"]


def test_the_lexicon_is_the_agents_says_and_hears(configured: Providers) -> None:
    words = Lexicon(said={"Vidal": "vidál", "GSA": "G S A"}, heard=("GSA", "Vidal"))
    config = apply_tuning(DECLARED, Tuning(), words, defaults=configured.defaults)
    assert config.says == {"Vidal": "vidál", "GSA": "G S A"}
    assert config.hears == ("GSA", "Vidal")


def test_a_model_and_a_voice_are_named_in_the_agents_own_words(configured: Providers) -> None:
    assert model_of("openai/gpt-5", "llm", in_use="anthropic") == Model("openai", "gpt-5")
    assert model_of(None, "llm", in_use="anthropic") is None
    voiced = apply_tuning(
        DECLARED, Tuning(tts="hume", voice="v-1"), NOTHING, defaults=configured.defaults
    )
    assert voiced.voice == Voice("hume", None, "v-1")
    assert apply_tuning(DECLARED, Tuning(), NOTHING, defaults=configured.defaults).voice is None


def test_a_voice_call_runs_ten_minutes_unless_the_world_says_otherwise(
    configured: Providers,
) -> None:
    def limit(tuning: Tuning) -> int:
        return apply_tuning(DECLARED, tuning, NOTHING, defaults=configured.defaults).max_duration_s

    assert limit(Tuning()) == 600
    assert limit(Tuning(max_duration_s=900)) == 900
    assert limit(Tuning(max_duration_s=0)) == 0


def test_the_language_is_the_worlds_and_unset_it_is_what_an_older_app_declared(
    configured: Providers,
) -> None:
    def language(declared: AgentConfig, tuning: Tuning) -> str | None:
        return apply_tuning(declared, tuning, NOTHING, defaults=configured.defaults).language

    assert language(DECLARED, Tuning(language="en")) == "en"
    assert language(DECLARED, Tuning()) == "es"
    assert language(AgentConfig(slug="clinica-norte"), Tuning()) is None


def test_the_models_deadline_is_the_worlds_and_unset_it_is_none(configured: Providers) -> None:
    def deadline(tuning: Tuning) -> float | None:
        return apply_tuning(DECLARED, tuning, NOTHING, defaults=configured.defaults).llm_timeout_s

    assert deadline(Tuning()) is None
    assert deadline(Tuning(llm_timeout_s=6.0)) == 6.0


def test_what_the_class_fixes_wins_over_the_settings_and_the_rest_is_still_theirs(
    configured: Providers,
) -> None:
    declared = AgentConfig(
        slug="clinica-norte",
        language="es",
        voice=Voice("cartesia", "sonic-2", "class-voice"),
        llm=Model("openai", "gpt-5.4-mini", builds="responses.LLM"),
        greeting=Greeting(say="De la clase."),
        says={"GSA": "G S A"},
        fixed=frozenset({"language", "voice", "llm", "greeting", "says"}),
    )
    tuning = Tuning(
        language="en",
        voice="org-voice",
        llm="anthropic/claude-sonnet-5",
        stt="deepgram/nova-3",
        greeting=Greeting(say="Del org."),
        hangup=Hangup(when="the caller says bye"),
    )
    words = Lexicon(said={"Vidal": "vidál"}, heard=("Vidal",))
    config = apply_tuning(declared, tuning, words, defaults=configured.defaults)
    assert (config.language, config.voice, config.llm) == ("es", declared.voice, declared.llm)
    assert (config.greeting, config.says) == (Greeting(say="De la clase."), {"GSA": "G S A"})
    assert config.stt == Model("deepgram", "nova-3")
    assert config.hangup == Hangup(when="the caller says bye")
    assert config.hears == ("Vidal",)


def test_a_temperature_and_a_plugins_own_name_the_vendor_in_use_when_none_is_set(
    configured: Providers,
) -> None:
    tuning = Tuning(
        temperature=0.2,
        llm_builds="responses.LLM",
        llm_options={"use_websocket": True},
        stt_options={"eot_timeout_ms": 900},
        tts="cartesia",
        tts_options={"speed": 1.1},
    )
    config = apply_tuning(DECLARED, tuning, NOTHING, defaults=configured.defaults)
    assert config.llm == Model(
        "anthropic", "", 0.2, builds="responses.LLM", options={"use_websocket": True}
    )
    assert config.stt == Model("deepgram", "", options={"eot_timeout_ms": 900})
    assert config.voice == Voice("cartesia", None, None, options={"speed": 1.1})
    named = apply_tuning(
        DECLARED,
        Tuning(llm="openai/gpt-5.4-mini", temperature=0.5),
        NOTHING,
        defaults=configured.defaults,
    )
    assert named.llm == Model("openai", "gpt-5.4-mini", 0.5)


def test_a_temperature_below_zero_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="temperature is a number from 0"):
        Tuning(temperature=-0.1)


def test_who_ends_the_turn_is_set_on_the_ears_in_use(configured: Providers) -> None:
    config = apply_tuning(
        DECLARED, Tuning(end_of_turn="smart-turn"), NOTHING, defaults=configured.defaults
    )
    assert config.stt == Model("deepgram", "", end_of_turn="smart-turn")
