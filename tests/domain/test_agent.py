"""Tests for an agent's declaration and what the org sets on top of it."""

import re
from dataclasses import fields
from datetime import UTC, datetime

import pytest

from pinecall.domain.agent import (
    BLANK,
    DEFAULT_LAYOUT,
    AgentConfig,
    Docs,
    Greeting,
    KnowledgeFile,
    Lexicon,
    MemoryPolicy,
    Model,
    PromptBlock,
    ToolSpec,
    Tuning,
    Turn,
    Version,
    Versions,
    Voice,
    block_hash,
)
from pinecall.domain.errors import DeclarationRefused
from tests.domain.conftest import (
    A_DAY_AND_A_TIME,
    FIND_PATIENT,
    read_tool,
)


def test_an_agent_is_named_by_its_slug() -> None:
    assert AgentConfig("clinica-norte").slug == "clinica-norte"
    for slug in ("", "Clínica Norte", "clinica_norte", "-norte", "norte-"):
        with pytest.raises(DeclarationRefused, match="slug"):
            AgentConfig(slug)


def test_the_prompt_is_the_default_layout_until_the_app_declares_one() -> None:
    assert AgentConfig("clinica-norte").prompt == DEFAULT_LAYOUT
    assert [block.name for block in DEFAULT_LAYOUT] == ["identity", "knowledge", "tools", "view"]
    faq = (PromptBlock("identity", "static"), PromptBlock("faq", "static"))
    assert AgentConfig("clinica-norte", prompt=faq).prompt == faq
    with pytest.raises(DeclarationRefused, match=r"prompt block names repeat: \['faq'\]"):
        AgentConfig(
            "clinica-norte", prompt=(PromptBlock("faq", "static"), PromptBlock("faq", "dynamic"))
        )


def test_tool_names_are_unique_within_an_agent() -> None:
    with pytest.raises(DeclarationRefused, match=r"repeat: \['find_patient'\]"):
        AgentConfig("clinica-norte", tools=(FIND_PATIENT, FIND_PATIENT))
    agent = AgentConfig("clinica-norte", tools=(FIND_PATIENT,))
    assert agent.tools_by_name == {"find_patient": FIND_PATIENT}


def test_a_state_field_is_the_tenants_unless_declared() -> None:
    agent = AgentConfig("clinica-norte", state_fields={"slots": "public", "patient": "pii"})
    assert agent.visibility_of("slots") == "public"
    assert agent.visibility_of("patient") == "pii"
    assert agent.visibility_of("booking") == "tenant"


def test_an_outside_event_reaches_the_agent_only_as_declared() -> None:
    agent = AgentConfig("clinica-norte", events={"slot.released": frozenset({"app"})})
    assert agent.accepts("slot.released", "app")
    assert not agent.accepts("slot.released", "participant")
    assert not agent.accepts("anything.else", "app")
    with pytest.raises(DeclarationRefused, match="names who may send it"):
        AgentConfig("clinica-norte", events={"slot.released": frozenset()})
    with pytest.raises(DeclarationRefused, match="names who may send it"):
        AgentConfig("clinica-norte", events={"": frozenset({"app"})})


def test_a_pronunciation_is_a_word_and_how_it_is_said() -> None:
    assert AgentConfig("clinica-norte", says={"GSA": "ge ese a"}).says == {"GSA": "ge ese a"}
    with pytest.raises(DeclarationRefused, match=r"agent clinica-norte: a pronunciation"):
        AgentConfig("clinica-norte", says={"GSA": ""})


def test_docs_are_retrieved_per_turn_or_behind_a_search_tool() -> None:
    assert (Docs("clinica").mode, Docs("clinica").k) == ("retrieved", 8)
    assert Docs("clinica", mode="tool").mode == "tool"
    with pytest.raises(DeclarationRefused, match="name the knowledge base"):
        Docs("")
    with pytest.raises(DeclarationRefused, match="at least one chunk"):
        Docs("clinica", k=0)
    with pytest.raises(DeclarationRefused, match="never negative"):
        Docs("clinica", min_score=-1)


def test_a_knowledge_file_is_named_by_its_path() -> None:
    assert KnowledgeFile("faq.md", "We open at nine.").path == "faq.md"
    with pytest.raises(DeclarationRefused, match="named by its path"):
        KnowledgeFile("", "We open at nine.")


def test_a_memory_policy_says_what_to_keep_and_what_never() -> None:
    policy = MemoryPolicy(remember=("alergias", "su médico habitual"), forget=("pagos",))
    assert "pagos" in policy.forget
    assert "alergias" in policy.remember
    with pytest.raises(DeclarationRefused, match="both remember and forget"):
        MemoryPolicy(remember=("pagos",), forget=("pagos",))


def test_a_whole_declaration_holds_together() -> None:
    agent = AgentConfig(
        "clinica-norte",
        name="Clínica Norte",
        language="es-ES",
        voice=Voice("cartesia", voice_id="v-1"),
        llm=Model("anthropic", "claude-sonnet-5", temperature=0.2),
        knowledge="# Clínica Norte\nHorario de 9 a 20.",
        bases=(Docs("clinica"),),
        memory=MemoryPolicy(remember=("alergias",)),
        tools=(FIND_PATIENT,),
    )
    assert agent.knowledge is not None
    assert agent.knowledge.startswith("# Clínica Norte")
    assert [docs.base for docs in agent.bases] == ["clinica"]
    assert agent.memory is not None
    assert agent.memory.remember == ("alergias",)
    assert agent.voice is not None
    assert agent.voice.provider == "cartesia"


def test_a_greeting_is_say_or_reply_and_never_both_or_neither() -> None:
    assert Greeting(say="Buenas.").say == "Buenas."
    assert Greeting(reply="saluda y preséntate").reply is not None
    with pytest.raises(DeclarationRefused, match="both were declared"):
        Greeting(say="Buenas.", reply="saluda y preséntate")
    with pytest.raises(DeclarationRefused, match="neither was"):
        Greeting()


# Deepgram rejects eager_eot_threshold > eot_threshold, which would leave the call without STT.
def test_a_turn_that_would_guess_later_than_it_decides_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="cannot sit above"):
        Turn(eot_threshold=0.7, eager_eot_threshold=0.9)


def test_how_long_a_barge_in_must_last_is_zero_or_more_milliseconds() -> None:
    assert Turn(min_interruption_ms=0).min_interruption_ms == 0
    assert Turn().min_interruption_ms is None
    with pytest.raises(DeclarationRefused, match="0 or more"):
        Turn(min_interruption_ms=-1)


def test_a_tool_runs_five_minutes_at_most() -> None:
    assert ToolSpec("free_slots", "Lists slots.", A_DAY_AND_A_TIME, timeout_s=300).timeout_s == 300
    for seconds in (0, 301, 86_400):
        with pytest.raises(DeclarationRefused, match="timeout_s is seconds above 0, 300 at most"):
            ToolSpec("free_slots", "Lists slots.", A_DAY_AND_A_TIME, timeout_s=seconds)


def test_no_count_of_the_turn_is_below_zero() -> None:
    assert Turn(endpointing_ms=0, min_interruption_words=0).endpointing_ms == 0
    with pytest.raises(DeclarationRefused, match="endpointing_ms is a count"):
        Turn(endpointing_ms=-5)
    with pytest.raises(DeclarationRefused, match="min_interruption_words is a count"):
        Turn(min_interruption_words=-1)


def test_the_eager_bar_may_sit_on_the_other_one() -> None:
    assert Turn(eot_threshold=0.85, eager_eot_threshold=0.85).eager_eot_threshold == 0.85
    assert Turn(eager_eot_threshold=0.4).eot_threshold is None


@pytest.mark.parametrize("seconds", [0, 60, 600, 3600])
def test_a_voice_calls_limit_is_none_or_a_minute_to_an_hour(seconds: int) -> None:
    assert AgentConfig("clinica-norte", max_duration_s=seconds).max_duration_s == seconds
    assert Tuning(max_duration_s=seconds).max_duration_s == seconds


@pytest.mark.parametrize("seconds", [30, 59, 3601, -1])
def test_a_limit_outside_it_is_refused_in_words_that_say_the_range(seconds: int) -> None:
    with pytest.raises(DeclarationRefused, match="0 for no limit, or 60 to 3600 seconds"):
        AgentConfig("clinica-norte", max_duration_s=seconds)
    with pytest.raises(DeclarationRefused, match="0 for no limit, or 60 to 3600 seconds"):
        Tuning(max_duration_s=seconds)


def test_no_deadline_for_the_model_is_the_default_and_a_deadline_is_positive_seconds() -> None:
    assert (AgentConfig("clinica-norte").llm_timeout_s, Tuning().llm_timeout_s) == (None, None)
    assert Tuning(llm_timeout_s=4.5).llm_timeout_s == 4.5
    for seconds in (0, -1.0):
        with pytest.raises(DeclarationRefused, match="positive number of seconds"):
            Tuning(llm_timeout_s=seconds)


def test_an_irreversible_tool_needs_a_confirm_template() -> None:
    with pytest.raises(DeclarationRefused, match="confirm template"):
        ToolSpec("book_slot", "Books a slot.", A_DAY_AND_A_TIME, side_effect="irreversible")
    booking = ToolSpec(
        "book_slot",
        "Books a slot.",
        A_DAY_AND_A_TIME,
        side_effect="irreversible",
        confirm="Le reservo el {day} a las {time}. ¿Lo confirmo?",
    )
    assert booking.confirm is not None


def test_a_read_tool_needs_no_yes_but_a_template_gates_any_tool() -> None:
    assert read_tool().confirm is None
    changed = ToolSpec("move_slot", "Moves a slot.", A_DAY_AND_A_TIME, confirm="¿Lo cambio?")
    assert changed.confirm is not None


def test_a_tool_without_a_description_is_one_no_model_can_choose() -> None:
    with pytest.raises(DeclarationRefused, match="description"):
        ToolSpec("free_slots", "   ", A_DAY_AND_A_TIME)


def test_when_is_the_apps_business_and_has_no_field_here() -> None:
    assert "when" not in {declared.name for declared in fields(ToolSpec)}


def test_nothing_set_is_every_knob_none_the_bases_with_the_rest() -> None:
    nothing = Tuning()
    assert (nothing.voice, nothing.llm, nothing.greeting, nothing.memory) == (None,) * 4
    assert nothing.knowledge is None
    assert nothing.bases is None
    assert Tuning(bases=()).bases == ()


@pytest.mark.parametrize(
    "knob", ["voice", "tts", "tts_model", "stt", "llm", "language", "knowledge"]
)
def test_a_blank_named_knob_is_refused_in_the_sentence_that_says_why(knob: str) -> None:
    blank: dict[str, str] = {knob: "   "}
    with pytest.raises(DeclarationRefused, match=re.escape(BLANK.format(field=knob))):
        Tuning(
            voice=blank.get("voice"),
            tts=blank.get("tts"),
            tts_model=blank.get("tts_model"),
            stt=blank.get("stt"),
            llm=blank.get("llm"),
            language=blank.get("language"),
            knowledge=blank.get("knowledge"),
        )


def test_an_opening_is_one_verb_here_as_it_is_on_the_class() -> None:
    with pytest.raises(DeclarationRefused, match="pick one"):
        Tuning(greeting=Greeting(say="Buenas.", reply="saluda y preséntate"))


def test_a_lexicon_refuses_a_blank_word_and_a_blank_spoken_form() -> None:
    assert Lexicon(said={"GSA": "ge ese a"}, heard=("Clínica Norte",)).heard == ("Clínica Norte",)
    with pytest.raises(DeclarationRefused, match="the lexicon"):
        Lexicon(said={"GSA": ""})
    with pytest.raises(DeclarationRefused, match="the lexicon"):
        Lexicon(heard=("Clínica Norte", " "))


def test_versions_none_is_a_corner_that_had_set_nothing() -> None:
    assert Versions() == Versions(config=None, lexicon=None)


def test_a_kept_value_carries_who_set_it_and_when() -> None:
    kept = Version("m_1", 3, "berna", None, datetime.now(UTC), Tuning(voice="clara"))
    assert (kept.version, kept.value.voice) == (3, "clara")


def test_a_blocks_hash_is_sha256_hex_and_says_nothing_of_the_text() -> None:
    text = block_hash("Sos la recepción")
    assert len(text) == 64
    assert all(character in "0123456789abcdef" for character in text)
    assert block_hash("Sos la recepción") == text
