"""The dial door over the real app: the guards, in order, and what a call back leaves behind."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.orgs.dialling import MemoryDialling
from pinecall.orgs.outbound import MemoryOutboundTrunks
from pinecall.routes.dispatching import MemoryDispatches, metadata_of
from pinecall.routes.table import MemoryRoutes
from pinecall.types import DialPolicy, OutboundTrunk, Route
from pinecall.types.dispatch import DIAL_KEY, DIRECTION_KEY, ORG_KEY
from tests.api.conftest import A_RECORD, AGENT

pytestmark = pytest.mark.unit

OURS = "+34910000000"
A_CALLER = "+34600123456"
A_STRANGER = "+34600999999"
DIAL = f"/v1/agents/{AGENT}/dial"
A_SOCKET = "app_dialling"


@pytest.fixture
def routes() -> MemoryRoutes:
    """The clinic answers one Spanish number in production: what a call back is shown as."""
    return MemoryRoutes([Route(org=A_RECORD.org, agent=AGENT, channel="phone", number=OURS)])


async def a_trunk(trunks: MemoryOutboundTrunks) -> None:
    """The org has already provisioned one: every test here is about the guards, not about that."""
    await trunks.put(
        OutboundTrunk(
            org=A_RECORD.org,
            kind="twilio",
            trunk_id="ST_out",
            address="pinecall-clinica.pstn.twilio.com",
            username="pinecall-clinica",
            password="never-printed",
        )
    )


async def a_caller_who_rang(
    store: MemoryStore, org: str = A_RECORD.org, agent: str = AGENT
) -> None:
    """One inbound call from A_CALLER, folded into the call index: the contact is known now."""
    call = f"call_rang_{org}"
    await store.owned(call, agent, org, "production", "")
    await store.append(
        call=call,
        agent=agent,
        type="call.ringing",
        data={
            "channel": "phone",
            "from": A_CALLER,
            "to": OURS,
            "caller": None,
            "route": {"channel": "phone", "number": OURS},
        },
    )


async def somebody_holding_it(registry: Registry) -> None:
    """An app socket holding the agent in production: without one, no call is placed at all."""
    await registry.register(
        owner=A_SOCKET,
        org=A_RECORD.org,
        env="production",
        slug=AGENT,
    )


async def ready(
    trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    """A trunk, a contact who called first, and somebody holding the agent: the floor of it all."""
    await a_trunk(trunks)
    await a_caller_who_rang(store)
    await dialling.put(A_RECORD.org, DialPolicy())
    await somebody_holding_it(registry)


async def test_a_call_back_opens_a_log_and_dispatches_a_job_into_the_room_the_call_names(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dispatches: MemoryDispatches,
    dialling: MemoryDialling,
) -> None:
    await ready(outbound_trunks, store, registry, dialling)
    answer = await tenant_http.post(DIAL, json={"to": A_CALLER})
    assert answer.status_code == 202, answer.text
    said: dict[str, Any] = answer.json()
    assert (said["agent"], said["to"], said["from"], said["env"]) == (
        AGENT,
        A_CALLER,
        OURS,
        "production",
    )
    # The log opens here and not in the worker: both numbers, the right way round, and who asked.
    entries = await store.since(said["call"])
    assert [entry.type for entry in entries] == ["call.dialing"]
    dialing = entries[0].data
    assert (dialing["from"], dialing["to"]) == (OURS, A_CALLER)
    assert dialing["asked_by"] == A_RECORD.key_id
    # One job, in a room named by the call, told what to dial and for how long.
    [job] = dispatches.jobs
    assert job.call == said["call"]
    told = metadata_of(job)
    assert told[ORG_KEY] == A_RECORD.org
    assert told[DIRECTION_KEY] == "outbound"
    assert told[DIAL_KEY] == {
        "trunk": "ST_out",
        "to": A_CALLER,
        "shown": OURS,
        "max_duration_s": DialPolicy().max_duration_s,
    }


async def test_every_dial_is_written_down_with_who_asked_for_it(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    """The ledger is the fraud trail: one row per dial, and a refused one is written too."""
    await ready(outbound_trunks, store, registry, dialling)
    await tenant_http.post(DIAL, json={"to": A_CALLER})
    await tenant_http.post(DIAL, json={"to": A_STRANGER})
    taken, refused = dialling.written
    assert (taken.dialled, taken.asked_by, taken.refused) == (A_CALLER, A_RECORD.key_id, None)
    assert (refused.dialled, refused.refused, refused.call) == (A_STRANGER, "stranger", None)


async def test_a_number_that_never_called_is_refused_until_an_operator_lifts_it(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dispatches: MemoryDispatches,
    dialling: MemoryDialling,
) -> None:
    """A call back goes BACK to somebody: the guard that makes a stolen key worth nothing."""
    await ready(outbound_trunks, store, registry, dialling)
    answer = await tenant_http.post(DIAL, json={"to": A_STRANGER})
    assert answer.status_code == 403
    detail: str = answer.json()["detail"]
    assert "never called or written to this org" in detail
    assert "dial_anywhere" in detail
    assert dispatches.jobs == []
    await dialling.put(A_RECORD.org, DialPolicy(dial_anywhere=True))
    assert (await tenant_http.post(DIAL, json={"to": A_STRANGER})).status_code == 202


async def test_a_country_the_org_has_no_number_in_is_the_carriers_to_refuse_and_not_ours(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    """A Spanish org dials a +1: which countries an account reaches is the carrier's setting
    (Twilio's geo permissions), and a second fence here once refused what Twilio allowed."""
    await ready(outbound_trunks, store, registry, dialling)
    await dialling.put(A_RECORD.org, DialPolicy(dial_anywhere=True))
    answer = await tenant_http.post(DIAL, json={"to": "+12125550123"})
    assert answer.status_code == 202, answer.text


async def test_a_satellite_range_and_a_number_that_is_not_one_are_refused_by_shape(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    """Where revenue-share fraud is dialled, refused before a table is read."""
    await ready(outbound_trunks, store, registry, dialling)
    satellite = await tenant_http.post(DIAL, json={"to": "+882345678901"})
    assert satellite.status_code == 400
    assert "satellite or global-service range" in satellite.json()["detail"]
    assert (await tenant_http.post(DIAL, json={"to": "600123456"})).status_code == 400


async def test_the_rate_guard_counts_the_refusals_too(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    """A burst of refused dials is the attack, so the window it is measured in holds them."""
    await ready(outbound_trunks, store, registry, dialling)
    await dialling.put(A_RECORD.org, DialPolicy(per_minute=2))
    assert (await tenant_http.post(DIAL, json={"to": A_STRANGER})).status_code == 403
    assert (await tenant_http.post(DIAL, json={"to": A_STRANGER})).status_code == 403
    third = await tenant_http.post(DIAL, json={"to": A_CALLER})
    assert third.status_code == 429
    assert "has placed 2 of its 2 outbound calls a minute" in third.json()["detail"]


async def test_a_days_worth_is_refused_in_its_own_words(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    await ready(outbound_trunks, store, registry, dialling)
    await dialling.put(A_RECORD.org, DialPolicy(per_day=1))
    assert (await tenant_http.post(DIAL, json={"to": A_CALLER})).status_code == 202
    second = await tenant_http.post(DIAL, json={"to": A_CALLER})
    assert second.status_code == 429
    assert "outbound calls a day" in second.json()["detail"]


async def test_the_refusals_that_are_about_this_box_rather_than_this_number(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
) -> None:
    """No trunk yet, and a `from` that is not a number this agent answers."""
    await a_caller_who_rang(store)
    await somebody_holding_it(registry)
    no_trunk = await tenant_http.post(DIAL, json={"to": A_CALLER})
    assert no_trunk.status_code == 409
    assert "no outbound trunk" in no_trunk.json()["detail"]
    await a_trunk(outbound_trunks)
    theirs = await tenant_http.post(DIAL, json={"to": A_CALLER, "from": "+34911111111"})
    assert theirs.status_code == 400
    assert "is not a number agent" in theirs.json()["detail"]


async def test_an_agent_nobody_is_holding_is_not_dialled_for(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    dispatches: MemoryDispatches,
) -> None:
    """A stranger's phone ringing for a conversation that cannot happen is worse than no call."""
    await a_trunk(outbound_trunks)
    await a_caller_who_rang(store)
    answer = await tenant_http.post(DIAL, json={"to": A_CALLER})
    assert answer.status_code == 409
    assert "nobody is holding" in answer.json()["detail"]
    assert dispatches.jobs == []


async def test_an_agent_with_no_phone_door_has_nothing_to_be_shown_as(
    tenant_http: httpx.AsyncClient, outbound_trunks: MemoryOutboundTrunks
) -> None:
    await a_trunk(outbound_trunks)
    answer = await tenant_http.post("/v1/agents/otra/dial", json={"to": A_CALLER})
    assert answer.status_code == 404
    assert "answers no phone number in production" in answer.json()["detail"]


async def test_another_orgs_caller_is_a_stranger_to_this_one(
    tenant_http: httpx.AsyncClient,
    outbound_trunks: MemoryOutboundTrunks,
    store: MemoryStore,
    registry: Registry,
    dialling: MemoryDialling,
) -> None:
    """The call index answers per org: a number that called somebody else has not called us."""
    await a_trunk(outbound_trunks)
    await dialling.put(A_RECORD.org, DialPolicy())
    await somebody_holding_it(registry)
    await a_caller_who_rang(store, org="org_somebody_else", agent="otra")
    answer = await tenant_http.post(DIAL, json={"to": A_CALLER})
    assert answer.status_code == 403
    assert "never called or written to this org" in answer.json()["detail"]
