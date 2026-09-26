"""The wire's declaration as the domain's: the contract lands, the environment is the world's."""

import pytest

from pinecall.providers.declaration import apply_declaration, changed_by
from pinecall.types import AgentConfig
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

CLARA = AgentConfig(slug="clinica-norte")


def test_the_language_the_layout_and_the_search_land_as_the_domain_holds_them() -> None:
    wire = defs.AgentConfig.model_validate(
        {
            "language": "es-ES",
            "uses_knowledge": True,
            "prompt": [{"name": "identity", "region": "static"}],
        }
    )
    agent = apply_declaration(CLARA, wire)
    assert (agent.language, agent.uses_knowledge) == ("es-ES", True)
    assert [block.name for block in agent.prompt] == ["identity"]
    assert changed_by(wire) == ("language", "prompt", "uses_knowledge")


def test_the_panel_declares_its_name_and_nothing_of_what_it_holds() -> None:
    wire = defs.AgentConfig.model_validate({"view": {"name": "Cliente"}})
    assert apply_declaration(CLARA, wire).view == "Cliente"
    # A class that stopped drawing one says so by sending null, and the agent draws none again.
    assert apply_declaration(CLARA, defs.AgentConfig.model_validate({"view": None})).view is None
    # A configure about something else leaves the panel exactly as it was.
    assert (
        apply_declaration(CLARA, defs.AgentConfig.model_validate({"language": "es"})).view is None
    )


# A class written for an older package still sends its voice, its models, an opening, a base: the
# gateway takes the frame and reads none of it, because every one of those is the world's now.
def test_the_environment_a_class_still_sends_is_taken_and_not_read() -> None:
    wire = defs.AgentConfig.model_validate(
        {
            "voice": {"name": "carolina"},
            "llm": {"provider": "anthropic", "model": "claude-haiku-4-5"},
            "greeting": {"say": "Hola."},
            "docs": {"base": "clinica-norte"},
            "memory": {"remember": ["preference"]},
            "knowledge": {"path": "./knowledge/clinica.md", "text": "Abrimos a las nueve."},
        }
    )
    agent = apply_declaration(CLARA, wire)
    assert (agent.voice, agent.llm, agent.greeting, agent.memory, agent.knowledge) == (None,) * 5
    assert agent.bases == ()


def test_a_configure_that_leaves_a_field_out_keeps_what_the_agent_declared_before() -> None:
    before = apply_declaration(CLARA, defs.AgentConfig.model_validate({"language": "es-ES"}))
    after = apply_declaration(before, defs.AgentConfig.model_validate({"uses_knowledge": True}))
    assert (after.language, after.uses_knowledge) == ("es-ES", True)
