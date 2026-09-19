"""Every base an agent reads was pushed to its world, or configure is refused naming the push."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.socket import NO_SUCH_BASE
from pinecall.orgs.tuning import MemoryTuning
from pinecall.types import PRODUCTION, Docs, KnowledgeFile, Tuning
from tests.api.agents.test_a_class_that_searches_needs_a_base import SEARCHES, configured
from tests.api.conftest import A_RECORD, AGENT
from tests.lookups.fake_knowledge import ScriptedKnowledge

pytestmark = pytest.mark.unit


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    """A gateway that keeps bases, with the clinic's pushed and no other."""
    kept = ScriptedKnowledge()
    kept.pushed["clinica"] = [KnowledgeFile("horarios.md", "Abrimos a las nueve.")]
    return kept


def attached(tuning: MemoryTuning, base: str) -> None:
    """The org's own corner of production attaches this base to the clinic."""
    asyncio.run(
        tuning.put(
            A_RECORD.org,
            PRODUCTION,
            "",
            AGENT,
            Tuning(knowledge=(Docs(base=base),)),
            author="k_1",
            note=None,
            if_version=None,
        )
    )


def test_a_base_the_world_attaches_and_nobody_pushed_is_refused_naming_the_push(
    gateway: TestClient, tuning: MemoryTuning
) -> None:
    attached(tuning, "precios")
    answer = configured(gateway, SEARCHES)
    assert answer["type"] == "error"
    assert answer["data"]["code"] == "refused"
    assert answer["data"]["message"] == NO_SUCH_BASE.format(
        slug=AGENT, base="precios", world=PRODUCTION
    )


def test_a_base_the_class_still_names_and_nobody_pushed_is_refused_too(gateway: TestClient) -> None:
    answer = configured(gateway, {"language": "es", "docs": {"base": "precios"}})
    assert answer["type"] == "error"
    assert "pinecall knowledge push ./knowledge/docs --base precios" in answer["data"]["message"]


def test_a_pushed_base_the_world_attaches_is_read(
    gateway: TestClient, tuning: MemoryTuning
) -> None:
    attached(tuning, "clinica")
    assert configured(gateway, SEARCHES)["type"] != "error"


def test_an_agent_that_reads_no_base_is_never_asked_about_one(gateway: TestClient) -> None:
    assert configured(gateway, {"language": "es"})["type"] != "error"
