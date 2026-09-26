"""The trunk an org places calls through: what is missing, the plan, and the writes it repeats."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.orgs.dial_policies_memory import MemoryDialling
from pinecall.orgs.outbound_credentials_memory import MemoryOutboundTrunks
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.settings import Settings
from pinecall.types import DialPolicy, Route
from pinecall_testkit.fake_media import MemoryOutbound
from pinecall_testkit.keys import A_VAULT_KEY
from tests.api.carriers import A_KEY_SID, A_SID, FakeTwilio
from tests.api.conftest import A_LIVEKIT, A_RECORD, AGENT, AN_OPS_KEY

pytestmark = pytest.mark.unit

OURS = "+34910000000"
OUTBOUND = "/v1/carrier/outbound"
TWILIO_BODY = {"kind": "twilio", "account_sid": A_SID, "user": A_KEY_SID, "secret": "s3cret"}
SIP_BODY: dict[str, Any] = {
    "kind": "sip",
    "username": "pbx",
    "password": "pw",
    "addresses": ["203.0.113.0/24"],
    "outbound_host": "sip.carrier.example:5060",
    "outbound_transport": "tls",
}
# The label is the org's, in the only alphabet Twilio takes for one, and the host follows from it.
THE_HOST = f"pinecall-{A_RECORD.org}.pstn.twilio.com"


@pytest.fixture
def settings() -> Settings:
    """A box with a name: what a carrier's trunk is pointed at."""
    return Settings(
        world="production",
        ops_key=AN_OPS_KEY,
        vault_key=A_VAULT_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        domain="box.pinecall.io",
    )


@pytest.fixture
def routes() -> MemoryRoutes:
    """Nothing imported yet: the number a call back is shown as is a step of its own."""
    return MemoryRoutes()


async def imported(routes: MemoryRoutes) -> None:
    """One number of the org's own, in production: without one nothing can be shown as it."""
    await routes.put(Route(org=A_RECORD.org, agent=AGENT, channel="phone", number=OURS))


async def brought(tenant_http: httpx.AsyncClient, body: dict[str, Any] = TWILIO_BODY) -> None:
    answer = await tenant_http.put("/v1/carrier", json=body)
    assert answer.status_code == 204, answer.text


async def test_an_org_with_no_carrier_is_told_every_step_it_still_owes(
    tenant_http: httpx.AsyncClient,
) -> None:
    said = (await tenant_http.get(OUTBOUND)).json()
    assert said["ready"] is False
    assert said["kind"] is None
    assert said["from_numbers"] == []
    assert "PUT /v1/carrier" in said["steps_missing"][0]
    assert any("imported no phone number" in step for step in said["steps_missing"])
    # The guards are the code's own defaults until an operator says otherwise.
    assert said["guards"] == {
        "dial_anywhere": False,
        "per_minute": DialPolicy().per_minute,
        "per_day": DialPolicy().per_day,
        "max_duration_s": DialPolicy().max_duration_s,
    }


async def test_a_dry_run_is_the_plan_and_writes_nothing(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    outbound: MemoryOutbound,
    routes: MemoryRoutes,
) -> None:
    await brought(tenant_http)
    await imported(routes)
    answer = await tenant_http.post(f"{OUTBOUND}?dry_run=true")
    assert answer.status_code == 200, answer.text
    said = answer.json()
    assert said["dry_run"] is True and said["ready"] is False
    assert said["steps"] == [
        f"trunk    pinecall-{A_RECORD.org} — created on account {A_SID}",
        f"terminal {THE_HOST} — set",
        f"login    pinecall-{A_RECORD.org} — created, its password kept under the vault key",
        (
            f"livekit  outbound trunk pinecall:{A_RECORD.org}:out → {THE_HOST} over auto, "
            "showing 1 of this org's numbers with SIP auth"
        ),
    ]
    assert twilio_account.made == [] and outbound.trunks == {}


async def test_the_twilio_steps_are_written_once_and_a_second_run_finds_them_standing(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    outbound: MemoryOutbound,
    outbound_trunks: MemoryOutboundTrunks,
    routes: MemoryRoutes,
) -> None:
    """Idempotent, like the import: the second run creates nothing and keeps the same password."""
    await brought(tenant_http)
    await imported(routes)
    first = (await tenant_http.post(OUTBOUND)).json()
    assert first["ready"] is True
    assert first["address"] == THE_HOST
    assert first["trunk"] == outbound.trunks[A_RECORD.org].trunk_id
    assert twilio_account.made == [
        f"trunk pinecall-{A_RECORD.org}",
        f"terminal pinecall-{A_RECORD.org}",
        f"login pinecall-{A_RECORD.org} as pinecall-{A_RECORD.org}",
        "trunked CL_1",
    ]
    kept = await outbound_trunks.of(A_RECORD.org)
    assert kept is not None and kept.password
    was = kept.password

    second = (await tenant_http.post(OUTBOUND)).json()
    assert second["steps"][:3] == [
        f"trunk    TK_1 pinecall-{A_RECORD.org} — standing",
        f"terminal {THE_HOST} — standing",
        f"login    CL_1 pinecall-{A_RECORD.org} — standing",
    ]
    assert "on the trunk already" in second["steps"][3]
    assert twilio_account.made[4:] == []
    again = await outbound_trunks.of(A_RECORD.org)
    assert again is not None and again.password == was


async def test_a_sip_peer_is_dialled_where_it_said_and_nothing_is_made_on_its_switch(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    outbound: MemoryOutbound,
    routes: MemoryRoutes,
) -> None:
    await brought(tenant_http, SIP_BODY)
    await imported(routes)
    said = (await tenant_http.post(OUTBOUND)).json()
    assert said["address"] == "sip.carrier.example:5060"
    assert said["steps"][0] == "peer     sip.carrier.example:5060 over tls, authenticating as pbx"
    assert twilio_account.made == []
    placed = outbound.trunks[A_RECORD.org].placing
    assert (placed.transport, placed.auth, placed.numbers) == ("tls", ("pbx", "pw"), (OURS,))


async def test_a_sip_peer_that_declared_no_outbound_host_is_refused_by_name(
    tenant_http: httpx.AsyncClient, routes: MemoryRoutes
) -> None:
    """The networks a peer sends calls FROM are not an address it accepts one AT."""
    await imported(routes)
    quiet = {name: value for name, value in SIP_BODY.items() if not name.startswith("outbound_")}
    await brought(tenant_http, quiet)
    missing = (await tenant_http.get(OUTBOUND)).json()["steps_missing"]
    assert any("declares no outbound host" in step for step in missing)
    answer = await tenant_http.post(OUTBOUND)
    assert answer.status_code == 409
    assert "declares no outbound host" in answer.json()["detail"]


async def test_a_trunk_provisioned_for_another_kind_of_carrier_is_said_to_be_stale(
    tenant_http: httpx.AsyncClient, routes: MemoryRoutes
) -> None:
    await brought(tenant_http)
    await imported(routes)
    assert (await tenant_http.post(OUTBOUND)).status_code == 200
    await brought(tenant_http, SIP_BODY)
    said = (await tenant_http.get(OUTBOUND)).json()
    assert said["ready"] is False
    assert any("provisioned for a twilio carrier" in step for step in said["steps_missing"])


async def test_the_read_door_says_ready_once_the_trunk_stands(
    tenant_http: httpx.AsyncClient, dialling: MemoryDialling, routes: MemoryRoutes
) -> None:
    await brought(tenant_http)
    await imported(routes)
    await dialling.put(A_RECORD.org, DialPolicy(dial_anywhere=True))
    await tenant_http.post(OUTBOUND)
    said = (await tenant_http.get(OUTBOUND)).json()
    assert said == {
        "ready": True,
        "kind": "twilio",
        "from_numbers": [OURS],
        "steps_missing": [],
        "guards": {
            "dial_anywhere": True,
            "per_minute": DialPolicy().per_minute,
            "per_day": DialPolicy().per_day,
            "max_duration_s": DialPolicy().max_duration_s,
        },
    }


async def test_one_orgs_trunk_is_never_another_orgs(
    tenant_http: httpx.AsyncClient, outbound_trunks: MemoryOutboundTrunks, routes: MemoryRoutes
) -> None:
    """Every table here is keyed by the org, and a key IS an org: another's reads as none."""
    await brought(tenant_http)
    await imported(routes)
    await tenant_http.post(OUTBOUND)
    assert await outbound_trunks.of("org_somebody_else") is None
