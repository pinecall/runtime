"""A quota refuses at the door: credits.exhausted in the agent's log, and a 429 that says why."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from livekit.agents import llm as agents
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import CallContext, Quotas, Route
from pinecall.types.org import Ceiling
from pinecall.worker.client import Gateway
from pinecall.worker.hop import GatewayRefused
from tests.api.calls.tokens.test_the_door import minted
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import a_caller, a_door, a_register, an_app, entry_until
from tests.api.test_worker_doors import declared
from tests.log.test_usage import A_SUMMARY
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

ORG = A_RECORD.org
ANOTHER_AGENT = "clinica-sur"
# One answer that read 80 tokens and wrote 20.
A_HUNDRED_TOKENS = agents.CompletionUsage(completion_tokens=20, prompt_tokens=80, total_tokens=100)


def a_call(call: str) -> CallContext:
    """One web call arriving for the clinic, under the id the worker was given."""
    return CallContext(
        call=call,
        channel="web",
        direction="inbound",
        caller="visitor_1",
        route=Route(org=ORG, agent=AGENT, channel="web"),
        today=date(2026, 9, 8),
    )


async def spent(store: MemoryStore, call: str, minutes: float) -> None:
    """One call already summarised in the org's log, this long."""
    await store.owned(call, AGENT, ORG)
    await store.append(call, AGENT, "call.summary", {**A_SUMMARY, "duration_s": minutes * 60})


async def the_refusal_in(store: MemoryStore, agent: str = AGENT) -> dict[str, Any]:
    """The newest credits.exhausted in the agent's own log, which is where a refusal is written."""
    refusals = [one for one in await store.agent_since(agent) if one.type == "credits.exhausted"]
    assert refusals, "nothing was refused"
    return refusals[-1].data


# ── minutes ─────────────────────────────────────────────────────────────────────


async def test_a_quota_of_n_minutes_refuses_the_call_after_them_with_a_429_that_says_why(
    worker_gateway: Gateway, registry: Registry, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    """Criterion 2: two minutes bought, two spent, and the next call is refused by name."""
    await declared(registry)
    await orgs.set_quotas(ORG, Quotas(minutes=2))
    await spent(store, "CA_first", 1.5)
    await worker_gateway.opened(a_call("CA_second"), AGENT)
    await worker_gateway.sealed("CA_second")
    await spent(store, "CA_third", 0.5)
    with pytest.raises(GatewayRefused) as refused:
        await worker_gateway.opened(a_call("CA_fourth"), AGENT)
    assert "429" in str(refused.value)
    assert f"org {ORG} has used 2 of its 2 minutes: credits.exhausted" in str(refused.value)
    assert await the_refusal_in(store) == {"org": ORG, "quota": "minutes", "used": 2.0, "limit": 2}


async def test_the_open_door_answers_what_is_left_of_the_orgs_minutes_in_seconds(
    worker_gateway: Gateway, registry: Registry, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    """Admission runs at the open: the worker ends the call where the minutes run out."""
    await declared(registry)
    await orgs.set_quotas(ORG, Quotas(minutes=2))
    await spent(store, "CA_first", 1.5)
    assert await worker_gateway.opened(a_call("CA_second"), AGENT) == Ceiling(seconds=30, minutes=2)


async def test_an_org_whose_minutes_nobody_limited_is_answered_no_ceiling(
    worker_gateway: Gateway, registry: Registry, orgs: MemoryOrgs
) -> None:
    await declared(registry)
    await orgs.set_quotas(ORG, Quotas(messages=100))
    assert await worker_gateway.opened(a_call("CA_one"), AGENT) is None


async def test_an_org_nobody_limited_is_never_refused(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    """A self-hosted box has the mechanism and no numbers: None is no limit."""
    await declared(registry)
    await spent(store, "CA_long", 10_000)
    await worker_gateway.opened(a_call("CA_next"), AGENT)
    assert [entry.type for entry in await store.agent_since(AGENT)][-1] != "credits.exhausted"


# ── tokens ──────────────────────────────────────────────────────────────────────


async def test_an_llm_tokens_quota_refuses_the_next_call_once_the_org_has_spent_them(
    worker_gateway: Gateway, registry: Registry, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    """A_SUMMARY read 1200 tokens and wrote 300: both count, and 1500 is the whole quota."""
    await declared(registry)
    await orgs.set_quotas(ORG, Quotas(llm_tokens=1500))
    await spent(store, "CA_first", 1.5)
    with pytest.raises(GatewayRefused, match="1500 of its 1500 llm_tokens"):
        await worker_gateway.opened(a_call("CA_second"), AGENT)
    assert (await the_refusal_in(store))["quota"] == "llm_tokens"


async def test_a_chat_is_refused_mid_conversation_once_its_own_turns_pass_the_token_quota(
    gateway: TestClient, orgs: MemoryOrgs, store: MemoryStore, llm: FakeLLM
) -> None:
    """The Meter has not seen this call yet — it folds at hang-up — and it is still counted."""
    await orgs.set_quotas(ORG, Quotas(llm_tokens=100))
    llm.script.append(Scripted(chunks=("Hola.",), usage=A_HUNDRED_TOKENS))
    with an_app(gateway) as app:
        app.send_json(a_register(AGENT, a_door("web")))
        app.receive_json()
        with a_caller(gateway) as caller, pytest.raises(WebSocketDisconnect) as refused:
            caller.send_json({"text": "hola"})
            while caller.receive_json()["type"] != "turn.agent":
                pass
            caller.send_json({"text": "¿y mañana?"})
            while True:
                caller.receive_json()
    assert "100 of its 100 llm_tokens: credits.exhausted" in refused.value.reason
    assert llm.requests == 1
    assert (await the_refusal_in(store))["quota"] == "llm_tokens"


# ── calls at once ───────────────────────────────────────────────────────────────


async def test_the_concurrent_calls_quota_counts_the_calls_open_here_and_frees_on_seal(
    worker_gateway: Gateway, registry: Registry, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    await declared(registry)
    await orgs.set_quotas(ORG, Quotas(concurrent_calls=1))
    await worker_gateway.opened(a_call("CA_one"), AGENT)
    with pytest.raises(GatewayRefused, match="1 of its 1 concurrent_calls"):
        await worker_gateway.opened(a_call("CA_two"), AGENT)
    assert (await the_refusal_in(store))["quota"] == "concurrent_calls"
    await worker_gateway.sealed("CA_one")
    await worker_gateway.opened(a_call("CA_two"), AGENT)


# ── agents ──────────────────────────────────────────────────────────────────────


async def test_the_agents_quota_refuses_the_register_and_a_socket_correcting_itself_is_not_one_more(
    gateway: TestClient, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    await orgs.set_quotas(ORG, Quotas(agents=1))
    with an_app(gateway) as app:
        app.send_json(a_register(AGENT, a_door("web")))
        assert app.receive_json()["type"] == "agent.registered"
        app.send_json(a_register(AGENT, a_door("web"), a_door("phone", "+34910000000")))
        assert app.receive_json()["type"] == "agent.registered", "the same agent, corrected"
        app.send_json(a_register(ANOTHER_AGENT, a_door("web")))
        refused = entry_until(app, "error")
        assert "1 of its 1 agents" in refused["data"]["message"]
    assert (await the_refusal_in(store, ANOTHER_AGENT))["quota"] == "agents"


# ── the two other doors a call opens through ────────────────────────────────────


async def test_the_chat_door_refuses_with_the_same_sentence_as_its_close_reason(
    gateway: TestClient, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    await orgs.set_quotas(ORG, Quotas(messages=0))
    with an_app(gateway) as app:
        app.send_json(a_register(AGENT, a_door("web")))
        app.receive_json()
        with a_caller(gateway) as caller, pytest.raises(WebSocketDisconnect) as refused:
            caller.receive_json()
    assert "credits.exhausted" in refused.value.reason
    assert (await the_refusal_in(store))["quota"] == "messages"


async def test_the_token_door_answers_429_before_a_browser_ever_joins(
    gateway: TestClient, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    await orgs.set_quotas(ORG, Quotas(concurrent_calls=0))
    with an_app(gateway) as app:
        app.send_json(a_register(AGENT, a_door("web")))
        app.receive_json()
        status, body = minted(gateway, bearer=A_KEY)
    assert status == 429
    assert "credits.exhausted" in body["detail"]
    assert (await the_refusal_in(store))["quota"] == "concurrent_calls"
