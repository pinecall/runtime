"""The extraction door: one hang-up per case, against the class the app socket is holding."""

from __future__ import annotations

import httpx
import pytest

from pinecall.live.registry import Registry
from pinecall.orgs.tuning_store_memory import MemoryTuning
from pinecall.types import PRODUCTION, MemoryPolicy, Tuning
from pinecall_protocol import defs
from tests.api.conftest import A_RECORD, AGENT
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

DOOR = f"/v1/agents/{AGENT}/memory/extraction"
AN_OWNER = "app_holding_the_clinic"

A_CARD = "4242 4242 4242 4242"

A_CALL = [
    ["caller", "Soy Marta, alérgica a la penicilina"],
    ["agent", "Anotado. ¿Le va bien el martes?"],
    ["caller", f"Mejor por la mañana, y le paso la Visa {A_CARD}"],
]

CLARAS_MEMORY = MemoryPolicy(
    remember=("cómo prefiere que le llamen", "alergias", "su médico habitual"), forget=("pagos",)
)

A_TOOL = defs.ToolSpec(name="book", description="Books a slot", parameters={"type": "object"})

WHAT_THE_MODEL_ANSWERED = (
    '[{"op": "add", "text": "Es alérgica a la penicilina", "category": "alergias"},'
    ' {"op": "add", "text": "Prefiere por la mañana", "category": "cómo prefiere que le llamen"},'
    f' {{"op": "add", "text": "Paga con la Visa {A_CARD}", "category": "pagos"}}]'
)


@pytest.fixture
def llm() -> FakeLLM:
    """The org's model, scripted: one answer per case, and no vendor is ever reached."""
    return FakeLLM(Scripted(chunks=(WHAT_THE_MODEL_ANSWERED,)))


async def declared(
    registry: Registry, tuning: MemoryTuning, memory: MemoryPolicy | None = CLARAS_MEMORY
) -> None:
    """The clinic on its socket, and the policy the goldens are judged against set in its world."""
    await registry.register(AN_OWNER, A_RECORD.org, PRODUCTION, AGENT)
    await registry.configure(AN_OWNER, PRODUCTION, AGENT, defs.AgentConfig(tools=[A_TOOL]))
    if memory is not None:
        await tuning.put(
            A_RECORD.org,
            PRODUCTION,
            "",
            AGENT,
            Tuning(memory=memory),
            author="k_1",
            note=None,
            if_version=None,
        )


async def test_a_case_is_one_hang_up_and_the_answer_says_what_memory_would_have_kept(
    tenant_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning, llm: FakeLLM
) -> None:
    await declared(registry, tuning)
    case = {
        "name": "anota la alergia y nunca la tarjeta",
        "said": A_CALL,
        "expect": {"writes": ["alergias"], "never": ["pagos"], "never_says": [A_CARD]},
    }

    answered = await tenant_http.post(DOOR, json={"cases": [case]})

    assert answered.status_code == 200
    body = answered.json()
    assert (body["agent"], body["cases"], body["held"]) == (AGENT, 1, 1)
    [result] = body["results"]
    assert result["held"] is True
    assert result["wrote"] == [
        "add · alergias · Es alérgica a la penicilina",
        "add · cómo prefiere que le llamen · Prefiere por la mañana",
    ]
    assert result["refused"] == [f"add · pagos · Paga con la Visa {A_CARD}"]
    # One request per case, and the call the golden wrote down is what it carried.
    assert llm.requests == 1
    assert "user: Soy Marta, alérgica a la penicilina" in (
        llm.asked[0].history[0].text_content or ""
    )


async def test_a_case_that_did_not_hold_is_named_and_the_count_says_so(
    tenant_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    case = {
        "name": "recuerda al médico",
        "said": A_CALL,
        "expect": {"writes": ["su médico habitual"]},
    }

    body = (await tenant_http.post(DOOR, json={"cases": [case]})).json()

    assert body["held"] == 0
    [result] = body["results"]
    assert result["held"] is False
    assert [one["check"] for one in result["broke"]] == ["writes"]


async def test_a_golden_that_names_a_category_the_class_never_declared_is_refused(
    tenant_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    case = {"name": "seguros", "said": A_CALL, "expect": {"writes": ["seguros"]}}

    refused = await tenant_http.post(DOOR, json={"cases": [case]})

    assert refused.status_code == 400
    assert "which this agent's memory policy does not keep" in refused.json()["detail"]


async def test_a_class_that_keeps_nothing_has_nothing_to_hold_to_a_golden(
    tenant_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning, memory=None)

    refused = await tenant_http.post(DOOR, json={"cases": []})

    assert refused.status_code == 400
    assert refused.json()["detail"] == (
        f"agent {AGENT} declares no memory.remember: there is nothing to extract"
    )


async def test_an_agent_no_app_is_holding_is_the_registrys_own_refusal(
    tenant_http: httpx.AsyncClient,
) -> None:
    refused = await tenant_http.post(DOOR, json={"cases": []})

    assert refused.status_code == 404
    assert refused.json()["detail"] == f"no app is holding agent {AGENT}"
