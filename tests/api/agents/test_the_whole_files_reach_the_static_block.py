"""The resolver reads the attached bases' whole files into the knowledge block, the world first."""

from __future__ import annotations

import pytest

from pinecall.api.agents.tuned import BETWEEN_FILES, tuned_for
from pinecall.orgs.tuning import MemoryTuning
from pinecall.types import SANDBOX, AgentConfig, Docs, KnowledgeFile, Tuning
from tests.lookups.fakes import ScriptedKnowledge

pytestmark = pytest.mark.unit

ORG, SLUG = "clinica", "clinica-norte"
BY_HEART = KnowledgeFile("clinica.md", "# Clínica\n\nAbrimos a las nueve.", "whole")
TARIFAS = KnowledgeFile("tarifas.md", "# Tarifas\n\nRevisión: 45 €.", "whole")
CUT = KnowledgeFile("faq.md", "# FAQ\n\nUna pregunta.")
BY_THE_CLASS = KnowledgeFile("old.md", "what the class carried")


async def test_the_whole_files_of_every_attached_base_are_joined_where_the_class_file_went() -> (
    None
):
    kept = MemoryTuning()
    await kept.put(
        ORG,
        SANDBOX,
        "",
        SLUG,
        Tuning(knowledge=(Docs(base="clinica"), Docs(base="tarifas"))),
        author="k_1",
        note=None,
        if_version=None,
    )
    knowledge = ScriptedKnowledge(pushed={"clinica": [BY_HEART, CUT], "tarifas": [TARIFAS]})
    resolved = await tuned_for(
        kept, ORG, SANDBOX, None, SLUG, AgentConfig(slug=SLUG, knowledge=BY_THE_CLASS), knowledge
    )
    assert resolved.config.knowledge == KnowledgeFile(
        "clinica.md", BY_HEART.text + BETWEEN_FILES + TARIFAS.text, "whole"
    )
    assert [docs.base for docs in resolved.config.bases] == ["clinica", "tarifas"]
    assert resolved.config.docs == Docs(base="clinica")


async def test_a_base_with_no_whole_file_leaves_the_class_file_standing() -> None:
    kept = MemoryTuning()
    await kept.put(
        ORG,
        SANDBOX,
        "",
        SLUG,
        Tuning(knowledge=(Docs(base="clinica"),)),
        author="k_1",
        note=None,
        if_version=None,
    )
    knowledge = ScriptedKnowledge(pushed={"clinica": [CUT]})
    resolved = await tuned_for(
        kept, ORG, SANDBOX, None, SLUG, AgentConfig(slug=SLUG, knowledge=BY_THE_CLASS), knowledge
    )
    assert resolved.config.knowledge == BY_THE_CLASS


async def test_no_knowledge_base_at_all_reads_nothing() -> None:
    kept = MemoryTuning()
    declared = AgentConfig(slug=SLUG, docs=Docs(base="clinica"), knowledge=BY_THE_CLASS)
    resolved = await tuned_for(kept, ORG, SANDBOX, None, SLUG, declared, None)
    assert resolved.config.knowledge == BY_THE_CLASS
    assert resolved.config.bases == (Docs(base="clinica"),)
