"""The two goldens a ring starts from, read without a key: the declaration and the prompt."""

from __future__ import annotations

from tests.evals.clinica import declared, prompt_at

# The stage the tenant's own capture calls 0. Named here too, because a reader of this file
# should not have to open the ring to know which state these assertions are about.
IDENTIFY = 0


def test_the_declaration_carries_every_tool_the_class_declares() -> None:
    config = declared()

    assert config.slug == "clinica-norte"
    assert sorted(config.tools_by_name) == ["book", "findPatient", "freeSlots", "transfer"]
    assert config.tools_by_name["findPatient"].parameter_names == frozenset({"name", "phone"})
    assert config.tools_by_name["findPatient"].pii == frozenset({"name", "phone"})


def test_booking_is_declared_irreversible_with_the_sentence_it_reads_back() -> None:
    book = declared().tools_by_name["book"]

    assert book.side_effect == "irreversible"
    assert book.requires_confirmation
    assert "¿Lo confirmo?" in (book.confirm or "")


def test_the_declaration_names_the_model_the_class_asked_for() -> None:
    llm = declared().llm

    assert llm is not None
    assert (llm.provider, llm.model) == ("anthropic", "claude-haiku-4-5-20251001")


def test_the_first_state_renders_a_prefix_with_the_rules_and_a_view_with_the_next_move() -> None:
    prompt = prompt_at(IDENTIFY)

    assert prompt.static.startswith("Eres la recepción de Clínica Norte.")
    assert "Una sola pregunta por turno" in prompt.static
    # The view is the dynamic region alone: the marker that opens it never travels with it.
    assert "── dynamic ──" not in prompt.view
    assert prompt.view.endswith("Nada más hasta identificar al paciente.")
