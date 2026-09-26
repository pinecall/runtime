"""The trunk a live call dials a second leg with: the org's guards answer before the SFU does."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.orgs.dial_policies import MemoryDialling
from pinecall.routes.outbound_trunks import Placing
from pinecall.types import DialPolicy
from tests.api.conftest import A_RECORD, AGENT
from tests.routes.fakes import MemoryOutbound

pytestmark = pytest.mark.unit

DOOR = f"/v1/agents/{AGENT}/outbound-trunk"
A_COLLEAGUE = "+34910000099"
ON_A_CALL = "call_somebody_is_on"


async def held(registry: Registry) -> None:
    """Somebody holds the agent in production: the door answers about a call being served."""
    await registry.register(owner="app_1", org=A_RECORD.org, env="production", slug=AGENT)


async def a_trunk_on_the_sfu(outbound: MemoryOutbound) -> None:
    """The org's outbound trunk, as the provisioning left it on the media plane."""
    placing = Placing(address="pinecall.pstn.twilio.com", numbers=("+34910000000",))
    await outbound.provisioned(A_RECORD.org, placing)


async def test_a_leg_the_guards_allow_gets_the_trunk_the_sfu_holds(
    tenant_http: httpx.AsyncClient,
    registry: Registry,
    outbound: MemoryOutbound,
    dialling: MemoryDialling,
) -> None:
    await held(registry)
    await a_trunk_on_the_sfu(outbound)
    await dialling.put(A_RECORD.org, DialPolicy())

    answer = await tenant_http.get(DOOR, params={"to": A_COLLEAGUE, "call": ON_A_CALL})

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"trunk": f"ST_{A_RECORD.org}_out"}
    # One ledger row, as a cold dial leaves: a leg is a dial and a bill reads them the same way.
    [row] = dialling.written
    assert (row.dialled, row.call, row.refused) == (A_COLLEAGUE, ON_A_CALL, None)


# The colleague an agent puts a caller through to has no reason to have ever rung the org, so the
# stranger fence is not this door's. The windows are: they are what caps a looping agent.
async def test_a_stranger_is_dialled_where_a_cold_call_to_them_would_be_refused(
    tenant_http: httpx.AsyncClient,
    registry: Registry,
    outbound: MemoryOutbound,
    dialling: MemoryDialling,
) -> None:
    await held(registry)
    await a_trunk_on_the_sfu(outbound)
    await dialling.put(A_RECORD.org, DialPolicy(dial_anywhere=False))

    answer = await tenant_http.get(DOOR, params={"to": A_COLLEAGUE, "call": ON_A_CALL})

    assert answer.status_code == 200, "a leg into a live call is not a cold call"


async def test_an_org_dialling_too_fast_is_refused_the_trunk_and_told_which_guard(
    tenant_http: httpx.AsyncClient,
    registry: Registry,
    outbound: MemoryOutbound,
    dialling: MemoryDialling,
) -> None:
    await held(registry)
    await a_trunk_on_the_sfu(outbound)
    await dialling.put(A_RECORD.org, DialPolicy(per_minute=1))

    first = await tenant_http.get(DOOR, params={"to": A_COLLEAGUE, "call": ON_A_CALL})
    second = await tenant_http.get(DOOR, params={"to": A_COLLEAGUE, "call": ON_A_CALL})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "dial.too_fast" in second.json()["detail"]
    assert [row.refused for row in dialling.written] == [None, "too_fast"]


async def test_a_number_nobody_could_dial_never_reaches_the_sfu(
    tenant_http: httpx.AsyncClient,
    registry: Registry,
    outbound: MemoryOutbound,
    dialling: MemoryDialling,
) -> None:
    await held(registry)
    await a_trunk_on_the_sfu(outbound)
    await dialling.put(A_RECORD.org, DialPolicy())

    answer = await tenant_http.get(DOOR, params={"to": "not a number", "call": ON_A_CALL})

    assert answer.status_code == 400
    assert [row.refused for row in dialling.written] == ["shape"]


async def test_the_door_is_about_an_agent_this_org_holds_and_no_other(
    tenant_http: httpx.AsyncClient, outbound: MemoryOutbound, dialling: MemoryDialling
) -> None:
    await a_trunk_on_the_sfu(outbound)
    await dialling.put(A_RECORD.org, DialPolicy())

    answer = await tenant_http.get(DOOR, params={"to": A_COLLEAGUE, "call": ON_A_CALL})

    assert answer.status_code == 404, "nobody is holding it: there is no call to dial into"
    assert dialling.written == [], "nothing was judged and nothing was written down"
