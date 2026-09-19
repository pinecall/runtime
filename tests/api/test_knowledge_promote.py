"""Promote a base: the sandbox's rows into production, the golden's recall standing between."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.knowledge_promote import FROM_THE_SANDBOX, NOTHING_TO_PROMOTE
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.orgs.tuning import MemoryTuning
from pinecall.types import PRODUCTION, SANDBOX, Docs, Tuning
from pinecall.types.member import ROLE_SCOPES
from tests.api.conftest import A_KEY, A_RECORD, over_the_asgi_app
from tests.lookups.fakes import ScriptedKnowledge, a_chunk

pytestmark = pytest.mark.unit

ANA_KEY = "pk_test_ana_promotes_a_base"
ANA = KeyRecord(
    key_id="k_ana", org=A_RECORD.org, env=SANDBOX, scopes=ROLE_SCOPES["developer"], subject="m_ana"
)
A_PUSH = {
    "files": [{"path": "tarifas.md", "text": "# Tarifas\n\nRevisión: 45 €.", "mode": "whole"}]
}
GOLDEN = {"golden": {"questions": [{"asks": "¿cuánto cuesta?", "expects": "tarifas.md"}], "k": 2}}
PROMOTE = "/v1/knowledge/clinica/promote"


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANA_KEY: ANA})


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    return ScriptedKnowledge(answers=[a_chunk("c1", "Tarifas", "Revisión: 45 €.")])


@pytest.fixture
async def ana(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {ANA_KEY}")
    yield http
    await http.aclose()


async def test_a_push_carries_each_files_mode(
    ana: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    assert (await ana.put("/v1/knowledge/clinica", json=A_PUSH)).status_code == 200
    [file] = knowledge.pushed["clinica"]
    assert (file.path, file.mode) == ("tarifas.md", "whole")


async def test_promoting_copies_the_sandboxs_base_into_production_when_the_golden_holds(
    ana: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    await ana.put("/v1/knowledge/clinica", json=A_PUSH)
    promoted = await ana.post(PROMOTE, json=GOLDEN)
    assert promoted.status_code == 200, promoted.text
    body = promoted.json()
    assert (body["base"], body["world"], body["chunks"]) == ("clinica", PRODUCTION, 2)
    # The fake keeps one base for both worlds, so production "had" it: both recalls were read.
    assert (body["recall_before"], body["recall_after"]) == (1.0, 1.0)
    assert knowledge.copied == [("clinica", PRODUCTION)]


async def test_no_golden_sent_copies_and_answers_no_figures(
    ana: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    await ana.put("/v1/knowledge/clinica", json=A_PUSH)
    body = (await ana.post(PROMOTE, json={})).json()
    assert (body["recall_before"], body["recall_after"]) == (None, None)
    assert knowledge.copied == [("clinica", PRODUCTION)]


async def test_a_base_the_sandbox_never_pushed_is_a_404_that_names_it(
    ana: httpx.AsyncClient,
) -> None:
    refused = await ana.post(PROMOTE, json={})
    assert refused.status_code == 404
    assert refused.json()["detail"] == NOTHING_TO_PROMOTE.format(base="clinica")


async def test_a_production_key_cannot_promote(tenant_http: httpx.AsyncClient) -> None:
    refused = await tenant_http.post(PROMOTE, json={})
    assert refused.status_code == 409
    assert refused.json()["detail"] == FROM_THE_SANDBOX


async def test_attached_lists_which_agents_read_each_base_off_their_newest_settings(
    tenant_http: httpx.AsyncClient, tuning: MemoryTuning
) -> None:
    org = A_RECORD.org
    await tuning.put(
        org,
        PRODUCTION,
        "",
        "clinica-norte",
        Tuning(knowledge=(Docs(base="clinica"),)),
        author="k_1",
        note=None,
        if_version=None,
    )
    await tuning.put(
        org,
        PRODUCTION,
        "",
        "clinica-sur",
        Tuning(knowledge=(Docs(base="clinica"), Docs(base="tarifas"))),
        author="k_1",
        note=None,
        if_version=None,
    )
    # A newer version that detaches the base is what counts, not the history.
    await tuning.put(
        org,
        PRODUCTION,
        "",
        "clinica-sur",
        Tuning(knowledge=(Docs(base="tarifas"),)),
        author="k_1",
        note=None,
        if_version=1,
    )
    answered = await tenant_http.get("/v1/knowledge/attached")
    assert answered.status_code == 200
    assert answered.json() == {
        "bases": [
            {"base": "clinica", "agents": ["clinica-norte"]},
            {"base": "tarifas", "agents": ["clinica-sur"]},
        ]
    }
