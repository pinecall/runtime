"""A door that cannot embed says so: the vendor, the URL and the reason, never a bare 500."""

from __future__ import annotations

import httpx
import pytest

from pinecall.providers.embedder import EmbedderUnreachable, WrongModel, WrongWidth
from tests.lookups.fakes import ScriptedKnowledge, ScriptedMemory

pytestmark = pytest.mark.unit

KNOWLEDGE = "/v1/knowledge/clinica"
A_PUSH = {"files": [{"path": "tarifas.md", "text": "# Tarifas\n\nRevisión: 45 €."}]}
CONTACT = "/v1/contacts/c_1/memory"

# The sentence a laptop with no TEI actually reads, word for word.
TEI_IS_DOWN = EmbedderUnreachable(
    "TEI at http://127.0.0.1:8081 did not answer: All connection attempts failed"
)
TOO_NARROW = WrongWidth(
    "all-MiniLM-L6-v2 answers 384-wide vectors; the tables are declared at 1024, "
    "the width every halfvec column holds"
)
ANOTHER_MODEL = WrongModel(
    "base clinica-norte was pushed with pplx-embed-context-v1-0.6b; "
    "this gateway embeds with BAAI/bge-m3: push it again"
)


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    """The knowledge base this gateway holds; a test hands it the refusal to raise."""
    return ScriptedKnowledge()


@pytest.fixture
def memory() -> ScriptedMemory:
    """The memory this gateway holds; a test hands it the refusal to raise."""
    return ScriptedMemory()


async def test_a_push_with_no_embedder_answers_503_with_the_vendor_the_url_and_the_reason(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    """What `pinecall knowledge push` prints on a Mac, where TEI has no image to run at all."""
    knowledge.failing = TEI_IS_DOWN
    refused = await tenant_http.put(KNOWLEDGE, json=A_PUSH)
    assert refused.status_code == 503
    assert refused.json()["detail"] == str(TEI_IS_DOWN)
    assert "Internal Server Error" not in refused.text


async def test_a_push_whose_embedder_answers_another_width_is_409_naming_the_model(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    knowledge.failing = TOO_NARROW
    refused = await tenant_http.put(KNOWLEDGE, json=A_PUSH)
    assert refused.status_code == 409
    assert refused.json()["detail"] == str(TOO_NARROW)


async def test_a_base_of_another_model_is_409_naming_both_models_and_the_way_out(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    knowledge.failing = ANOTHER_MODEL
    refused = await tenant_http.put(KNOWLEDGE, json=A_PUSH)
    assert refused.status_code == 409
    assert refused.json()["detail"] == str(ANOTHER_MODEL)
    assert "push it again" in refused.text


async def test_it_is_every_door_and_not_one_so_a_contacts_memory_says_the_same(
    tenant_http: httpx.AsyncClient, memory: ScriptedMemory
) -> None:
    """One table maps the refusal to the status (api/_refusals.py); no endpoint writes a catch."""
    memory.failing = TEI_IS_DOWN
    refused = await tenant_http.get(CONTACT)
    assert refused.status_code == 503
    assert refused.json()["detail"] == str(TEI_IS_DOWN)
