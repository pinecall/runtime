"""The dispatch spends the token: the first POST /v1/calls opens the call, the second is refused."""

from __future__ import annotations

import time

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.tokens.ledger import MemoryTokens, TokenRecord
from pinecall.tokens.spend import TOKEN_SPENT
from pinecall.types import PRODUCTION, CallContext, Route
from pinecall.types.dispatch import AGENT_KEY, SCOPE_KEY
from pinecall.worker.gateway_client import Gateway
from pinecall.worker.gateway_http import GatewayRefused
from pinecall_protocol import defs
from tests.api.conftest import A_RECORD, AGENT
from tests.api.test_worker_doors import a_context

pytestmark = pytest.mark.unit

A_TOKENS_CALL = "call_minted_by_the_token_door"
AN_OWNER = "app_the_spending_tests"


async def held(registry: Registry) -> None:
    """The clinic, held by its app socket. Every agent is on the web: there is no door to hold."""
    await registry.register(AN_OWNER, A_RECORD.org, PRODUCTION, AGENT)
    await registry.configure(AN_OWNER, PRODUCTION, AGENT, defs.AgentConfig(language="es"))


def a_token_born_call(call: str = A_TOKENS_CALL) -> CallContext:
    """What the worker's router makes of a job whose dispatch a token wrote: the scope is in it."""
    return CallContext(
        call=call,
        channel="web",
        direction="inbound",
        caller="web_the_visitor",
        route=Route(org=A_RECORD.org, agent=AGENT, channel="web"),
        today=a_context().today,
        metadata={AGENT_KEY: AGENT, SCOPE_KEY: "talk"},
    )


def a_minted(call: str = A_TOKENS_CALL) -> TokenRecord:
    return TokenRecord(
        call=call,
        org=A_RECORD.org,
        agent=AGENT,
        scope="talk",
        expires_at=time.time() + 60,
    )


async def test_a_token_opens_its_call_once_and_the_second_dispatch_is_refused_with_the_reason(
    worker_gateway: Gateway, registry: Registry, tokens: MemoryTokens, store: MemoryStore
) -> None:
    """Criterion 2: the same token joined again is a second dispatch: 409, and the log says why."""
    await held(registry)
    await tokens.minted(a_minted())
    await worker_gateway.opened(a_token_born_call(), AGENT)
    with pytest.raises(GatewayRefused, match="409") as refused:
        await worker_gateway.opened(a_token_born_call(), AGENT)
    assert "already opened by its token" in str(refused.value)
    agents_log = await store.agent_since(AGENT)
    reasons = [entry for entry in agents_log if entry.type == "error"]
    assert [entry.data["code"] for entry in reasons] == [TOKEN_SPENT]
    assert A_TOKENS_CALL in str(reasons[0].data["message"])


async def test_a_dispatch_claiming_a_token_this_runtime_never_minted_is_refused(
    worker_gateway: Gateway, registry: Registry
) -> None:
    """A signed dispatch with a scope and no row: minted elsewhere, or before a dev restart."""
    await held(registry)
    with pytest.raises(GatewayRefused, match="never minted"):
        await worker_gateway.opened(a_token_born_call("call_nobody_minted"), AGENT)


async def test_a_phone_call_carries_no_scope_and_is_never_asked(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    """The media plane names a phone call's room: no token to spend, and none is looked for."""
    await held(registry)
    await worker_gateway.opened(a_context(), AGENT)
    assert [entry.type for entry in await store.since(a_context().call)] == ["call.ringing"]
    assert [entry.type for entry in await store.agent_since(AGENT)] == [
        "agent.registered",
        "agent.configured",
    ]
