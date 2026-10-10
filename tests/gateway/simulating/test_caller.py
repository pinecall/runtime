"""Tests for the simulated caller: a call id nobody opened, and no line once the call is over."""

import pytest

from pinecall.domain.errors import Conflict
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals.callers import Spending
from pinecall.gateway.simulating.caller import Caller, a_new_call, improvising, new_call
from pinecall.log.store import Claim
from pinecall.providers import catalog
from pinecall.wire.rest.evals import CallerPersona
from tests.conftest import AGENT, Knocking, configured, postgres
from tests.fakes.acme import AcmeLLM

APURADO = CallerPersona(name="apurado", goal="un turno", style="breve")


@postgres
async def test_a_simulated_caller_is_put_on_a_call_nobody_opened_and_never_on_one_that_is(
    knocking: Knocking,
) -> None:
    scope = Scope(knocking.org.id, "sandbox")
    await a_new_call(knocking.gateway, "call_new", AGENT, scope)
    with pytest.raises(Conflict, match="call_new exists already"):
        await new_call(knocking.gateway, "call_new")
    store = knocking.gateway.logs.store
    await store.claim("call_other", AGENT, knocking.org.id, Claim(scope))
    with pytest.raises(Conflict):
        await a_new_call(knocking.gateway, "call_other", AGENT, scope)


# A call the agent or a person already ended asks the caller's model for nothing more.
@postgres
async def test_a_call_already_over_asks_the_caller_for_no_line(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    await catalog.configure(pool, configured())
    scope = Scope(knocking.org.id, "sandbox")
    await a_new_call(knocking.gateway, "call_over", AGENT, scope)
    log = knocking.gateway.logs.writing("call_over", AGENT)
    ended: JsonObject = {
        "reason": "agent_hung_up",
        "ended_by": "agent",
        "ended_at": 9.0,
        "duration_s": 8.0,
    }
    await log.append("call.ended", ended)
    model = AcmeLLM(api_key="k")
    caller = Caller(APURADO, model, Spending(await catalog.providers(pool)))
    line = await improvising(knocking.gateway, "call_over", caller)(3)
    assert line == ("", True)
    assert model.requests == []
