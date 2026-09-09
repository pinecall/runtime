"""A quota refuses at the door: credits.exhausted in the agent's log, and a 429 that says why."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import CallContext, Quotas, Route
from pinecall.worker.client import Gateway, GatewayRefused
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import a_caller, a_door, a_register, an_app, entry_until
from tests.api.test_worker_doors import declared
from tests.api.tokens.test_the_door import minted
from tests.log.test_usage import A_SUMMARY

pytestmark = pytest.mark.unit

ORG = A_RECORD.org
ANOTHER_AGENT = "clinica-sur"


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


async def test_an_org_nobody_limited_is_never_refused(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    """A self-hosted box has the mechanism and no numbers: None is no limit."""
    await declared(registry)
    await spent(store, "CA_long", 10_000)
    await worker_gateway.opened(a_call("CA_next"), AGENT)
    assert [entry.type for entry in await store.agent_since(AGENT)][-1] != "credits.exhausted"


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
