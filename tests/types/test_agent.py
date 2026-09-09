"""AgentConfig: what an app declares, and the rules the runtime holds the declaration to."""

from typing import Any

import pytest

from pinecall.types import AgentConfig, DeclarationRefused, Docs, MemoryPolicy, ToolSpec

pytestmark = pytest.mark.unit

FIND_PATIENT = ToolSpec("find_patient", "Finds a patient by name and phone.", {"type": "object"})


def an_agent(**declared: Any) -> AgentConfig:
    """Clínica Norte with whatever the test declares on top."""
    given: dict[str, Any] = {"slug": "clinica-norte", **declared}
    return AgentConfig(**given)


def test_an_agent_is_named_by_its_slug() -> None:
    assert an_agent().slug == "clinica-norte"
    for slug in ("", "Clínica Norte", "clinica_norte", "-norte", "norte-"):
        with pytest.raises(DeclarationRefused, match="slug"):
            an_agent(slug=slug)


def test_channels_are_the_three_doors() -> None:
    three = frozenset({"phone", "web", "whatsapp"})
    assert an_agent(channels=three).channels == three
    assert an_agent().channels == frozenset()
    with pytest.raises(DeclarationRefused, match=r"unknown channels \['sms'\]"):
        an_agent(channels=frozenset({"sms"}))


def test_tool_names_are_unique_within_an_agent() -> None:
    with pytest.raises(DeclarationRefused, match=r"repeat: \['find_patient'\]"):
        an_agent(tools=(FIND_PATIENT, FIND_PATIENT))
    assert an_agent(tools=(FIND_PATIENT,)).tools_by_name == {"find_patient": FIND_PATIENT}


def test_a_state_field_is_the_tenants_unless_declared() -> None:
    agent = an_agent(state_fields={"slots": "public", "patient": "pii"})
    assert agent.visibility_of("slots") == "public"
    assert agent.visibility_of("patient") == "pii"
    assert agent.visibility_of("booking") == "tenant"
    with pytest.raises(DeclarationRefused, match="visibility is one of"):
        an_agent(state_fields={"slots": "everyone"})


def test_an_outside_event_reaches_the_agent_only_as_declared() -> None:
    agent = an_agent(events={"slot.released": frozenset({"app"})})
    assert agent.accepts("slot.released", "app")
    assert not agent.accepts("slot.released", "participant")
    assert not agent.accepts("anything.else", "app")
    undeclared: list[dict[str, Any]] = [
        {"slot.released": frozenset()},
        {"slot.released": frozenset({"mars"})},
        {"": frozenset({"app"})},
    ]
    for events in undeclared:
        with pytest.raises(DeclarationRefused, match="names who may send it"):
            an_agent(events=events)


def test_docs_are_retrieved_per_turn_or_behind_a_search_tool() -> None:
    assert (Docs("clinica").mode, Docs("clinica").k) == ("retrieved", 8)
    assert Docs("clinica", mode="tool").mode == "tool"
    refused: list[dict[str, Any]] = [
        {"base": ""},
        {"base": "clinica", "mode": "graph"},
        {"base": "clinica", "k": 0},
        {"base": "clinica", "min_score": -1},
    ]
    for row in refused:
        with pytest.raises(DeclarationRefused):
            Docs(**row)


def test_a_memory_policy_says_what_to_keep_and_what_never() -> None:
    policy = MemoryPolicy(remember=("alergias", "su médico habitual"), forget=("pagos",))
    assert "pagos" in policy.forget and "alergias" in policy.remember
    with pytest.raises(DeclarationRefused, match="both remember and forget"):
        MemoryPolicy(remember=("pagos",), forget=("pagos",))


def test_a_whole_declaration_holds_together() -> None:
    agent = an_agent(
        name="Clínica Norte",
        channels=frozenset({"phone", "web"}),
        language="es-ES",
        knowledge="# Clínica Norte\nHorario de 9 a 20.",
        docs=Docs("clinica"),
        memory=MemoryPolicy(remember=("alergias",)),
        tools=(FIND_PATIENT,),
    )
    assert agent.docs is not None and agent.docs.base == "clinica"
    assert agent.memory is not None and agent.memory.remember == ("alergias",)
