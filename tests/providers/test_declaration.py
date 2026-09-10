"""The wire's declaration as the domain's: the three ms-9 fields land as the shapes they are."""

import pytest

from pinecall.providers.declaration import changed_by, configured
from pinecall.types import AgentConfig, Docs, Greeting, KnowledgeFile, MemoryPolicy
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

CLARA = AgentConfig(slug="clinica-norte", channels=frozenset({"web"}))


def test_the_knowledge_file_the_docs_and_the_memory_policy_land_as_the_domain_holds_them() -> None:
    wire = defs.AgentConfig.model_validate(
        {
            "knowledge": {"path": "./knowledge/clinica.md", "text": "Abrimos a las nueve."},
            "docs": {"base": "clinica-norte", "k": 4, "min_score": 0.02},
            "memory": {"remember": ["preference", "health"], "forget": ["religion"]},
        }
    )
    agent = configured(CLARA, wire)
    assert agent.knowledge == KnowledgeFile("./knowledge/clinica.md", "Abrimos a las nueve.")
    assert agent.docs == Docs(base="clinica-norte", mode="retrieved", k=4, min_score=0.02)
    assert agent.memory == MemoryPolicy(remember=("preference", "health"), forget=("religion",))
    assert changed_by(wire) == ("docs", "knowledge", "memory")


def test_a_configure_that_leaves_the_three_out_keeps_what_the_agent_declared_before() -> None:
    before = configured(
        CLARA,
        defs.AgentConfig.model_validate({"docs": {"base": "clinica-norte"}}),
    )
    after = configured(before, defs.AgentConfig.model_validate({"greeting": {"say": "Hola."}}))
    assert after.docs == Docs(base="clinica-norte")
    assert after.greeting == Greeting(say="Hola.")


def test_the_three_sent_as_null_clear_what_was_declared() -> None:
    before = configured(
        CLARA,
        defs.AgentConfig.model_validate({"memory": {"remember": ["preference"]}}),
    )
    after = configured(before, defs.AgentConfig.model_validate({"memory": None}))
    assert after.memory is None
