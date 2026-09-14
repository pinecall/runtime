"""The org's numbers over the real app: a carrier brought, a number imported, a number let go."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.numbers import (
    ALREADY_THERE,
    NO_CARRIER,
    NO_DOMAIN,
    NOT_ON_ACCOUNT,
    NOT_THIS_ORGS,
    NOT_VERIFIED,
)
from pinecall.orgs.carriers import MemoryCarriers
from pinecall.routes.table import MemoryRoutes
from pinecall.routes.trunks import MemoryTrunks
from pinecall.routes.twilio import TWILIO_SIGNALLING
from pinecall.types import PRODUCTION, SANDBOX
from tests.api.carriers import A_KEY_SID, A_SID, FakeTwilio
from tests.api.conftest import A_LIVEKIT, A_RECORD, A_VAULT_KEY, AGENT, AN_OPS_KEY

pytestmark = pytest.mark.unit

ABAI = "+14176743169"
TWILIO_BODY = {"kind": "twilio", "account_sid": A_SID, "user": A_KEY_SID, "secret": "s3cret"}
SIP_BODY = {"kind": "sip", "username": "pbx", "password": "pw", "addresses": ["203.0.113.0/24"]}


@pytest.fixture
def settings() -> Settings:
    """A box with a name: what a carrier's trunk is pointed at."""
    return Settings(
        ops_key=AN_OPS_KEY,
        vault_key=A_VAULT_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        domain="box.pinecall.io",
    )


async def brought(tenant_http: httpx.AsyncClient, body: dict[str, Any] = TWILIO_BODY) -> None:
    answer = await tenant_http.put("/v1/carrier", json=body)
    assert answer.status_code == 204, answer.text


async def test_a_carrier_is_brought_verified_listed_by_account_and_taken_back(
    tenant_http: httpx.AsyncClient, twilio_account: FakeTwilio
) -> None:
    assert (await tenant_http.get("/v1/carrier")).status_code == 404
    await brought(tenant_http)
    said = (await tenant_http.get("/v1/carrier")).json()
    assert said == {"kind": "twilio", "account": A_SID}
    assert "s3cret" not in (await tenant_http.get("/v1/carrier")).text
    twilio_account.opens = False
    refused = await tenant_http.put("/v1/carrier", json=TWILIO_BODY)
    assert (refused.status_code, refused.json()["detail"]) == (400, NOT_VERIFIED)
    assert (await tenant_http.delete("/v1/carrier")).status_code == 204
    assert (await tenant_http.delete("/v1/carrier")).status_code == 404


async def test_a_bad_sid_is_refused_in_the_domains_words(tenant_http: httpx.AsyncClient) -> None:
    refused = await tenant_http.put("/v1/carrier", json={**TWILIO_BODY, "account_sid": "AC1"})
    assert refused.status_code == 400 and "34 characters" in refused.json()["detail"]


async def test_the_available_numbers_are_the_accounts_minus_the_imported(
    tenant_http: httpx.AsyncClient,
) -> None:
    assert (await tenant_http.get("/v1/numbers/available")).status_code == 404
    await brought(tenant_http)
    said = (await tenant_http.get("/v1/numbers/available")).json()
    assert said["kind"] == "twilio"
    assert [(n["number"], n["imported"]) for n in said["numbers"]] == [
        (ABAI, False),
        ("+34910000000", False),
    ]
    await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    said = (await tenant_http.get("/v1/numbers/available")).json()
    assert [(n["number"], n["imported"]) for n in said["numbers"]][0] == (ABAI, True)


async def test_a_dry_run_is_the_plan_and_writes_nothing(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    trunks: MemoryTrunks,
    routes: MemoryRoutes,
) -> None:
    await brought(tenant_http)
    answer = await tenant_http.post(
        "/v1/numbers?dry_run=true", json={"number": ABAI, "agent": AGENT}
    )
    assert answer.status_code == 200, answer.text
    said = answer.json()
    assert said["dry_run"] is True
    assert said["steps"] == [
        f"trunk    pinecall-{A_RECORD.org} — created on account {A_SID}",
        "origin   sip:box.pinecall.io:5060;transport=udp — set",
        f"number   {ABAI} — attached to the trunk",
        f"livekit  inbound trunk pinecall-{A_RECORD.org}: +{ABAI}, from "
        f"{len(TWILIO_SIGNALLING)} networks; one room per caller",
        f"route    {ABAI} phone → {AGENT} in production",
    ]
    assert twilio_account.made == [] and trunks.trunks == {}
    assert await routes.of_org(A_RECORD.org, PRODUCTION) == ()


async def test_an_import_writes_the_carriers_trunk_the_sfus_trunk_and_the_route_once(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    trunks: MemoryTrunks,
    routes: MemoryRoutes,
) -> None:
    await brought(tenant_http)
    first = await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    assert first.status_code == 200, first.text
    assert twilio_account.made == [
        f"trunk pinecall-{A_RECORD.org}",
        "origin sip:box.pinecall.io:5060;transport=udp",
        f"attach {ABAI}",
    ]
    assert trunks.trunks[A_RECORD.org].numbers == {ABAI}
    assert trunks.trunks[A_RECORD.org].allowed == TWILIO_SIGNALLING
    (route,) = await routes.of_org(A_RECORD.org, PRODUCTION)
    assert (route.number, route.agent, route.channel, route.env) == (
        ABAI,
        AGENT,
        "phone",
        PRODUCTION,
    )
    # A second import of the same number finds everything standing and makes nothing twice.
    again = await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": "tienda-sur"})
    assert again.status_code == 200
    assert again.json()["steps"][:3] == [
        f"trunk    TK_1 pinecall-{A_RECORD.org} — standing",
        "origin   sip:box.pinecall.io:5060;transport=udp — standing",
        f"number   {ABAI} — on the trunk already",
    ]
    assert len(twilio_account.made) == 3
    (moved,) = await routes.of_org(A_RECORD.org, PRODUCTION)
    assert moved.agent == "tienda-sur", "the route moved to the agent named"


async def test_a_sip_peer_touches_no_carrier_and_rides_its_credentials_onto_the_sfu(
    tenant_http: httpx.AsyncClient, twilio_account: FakeTwilio, trunks: MemoryTrunks
) -> None:
    await brought(tenant_http, SIP_BODY)
    assert (await tenant_http.get("/v1/numbers/available")).json() == {"kind": "sip", "numbers": []}
    answer = await tenant_http.post("/v1/numbers", json={"number": "+59829000000", "agent": AGENT})
    assert answer.status_code == 200, answer.text
    assert twilio_account.made == []
    admitted = trunks.trunks[A_RECORD.org]
    assert (admitted.allowed, admitted.auth) == (("203.0.113.0/24",), ("pbx", "pw"))
    assert "with SIP auth" in answer.json()["steps"][0]


async def test_the_refusals_name_what_is_missing(tenant_http: httpx.AsyncClient) -> None:
    nobody = await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    assert (nobody.status_code, nobody.json()["detail"]) == (404, NO_CARRIER)
    await brought(tenant_http)
    stranger = await tenant_http.post(
        "/v1/numbers", json={"number": "+15550000000", "agent": AGENT}
    )
    assert stranger.status_code == 404
    assert stranger.json()["detail"] == NOT_ON_ACCOUNT.format(number="+15550000000", account=A_SID)
    widget = await tenant_http.post(
        "/v1/numbers", json={"number": ABAI, "agent": AGENT, "channel": "web"}
    )
    assert widget.status_code == 400 and "phone or whatsapp" in widget.json()["detail"]
    bad = await tenant_http.post("/v1/numbers", json={"number": "abc", "agent": AGENT})
    assert bad.status_code == 400 and "E.164" in bad.json()["detail"]


async def test_a_box_with_no_name_cannot_be_pointed_at(
    tenant_http: httpx.AsyncClient, settings: Settings
) -> None:
    from pinecall.api import _deps
    from pinecall.api.app import app

    await brought(tenant_http)
    nameless = settings.model_copy(update={"domain": None})
    app.dependency_overrides[_deps.a_settings] = lambda: nameless
    answer = await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    assert (answer.status_code, answer.json()["detail"]) == (503, NO_DOMAIN)


async def test_letting_a_number_go_removes_the_route_and_the_admission_but_not_the_carrier(
    tenant_http: httpx.AsyncClient,
    trunks: MemoryTrunks,
    routes: MemoryRoutes,
    carriers: MemoryCarriers,
) -> None:
    await brought(tenant_http)
    await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    assert (await tenant_http.delete(f"/v1/numbers/{ABAI}")).status_code == 204
    assert await routes.of_org(A_RECORD.org, PRODUCTION) == ()
    assert trunks.trunks[A_RECORD.org].numbers == set()
    assert await carriers.of(A_RECORD.org) is not None
    assert (await tenant_http.delete(f"/v1/numbers/{ABAI}")).status_code == 404


async def test_the_numbers_listing_names_the_source_of_each_door(
    tenant_http: httpx.AsyncClient,
) -> None:
    await brought(tenant_http)
    await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    listed = (await tenant_http.get("/v1/numbers")).json()
    assert [(door["route"]["number"], door["source"]) for door in listed] == [(ABAI, "operator")]


# An org buys ONE number. Pointing it at the sandbox for an afternoon is how a team tries a new
# agent on the real line, and it is the whole reason staging needs no second world and no second
# bill: the carrier and the SFU are untouched, and the row says which world answers.
async def test_a_number_moves_between_the_worlds_and_back(
    tenant_http: httpx.AsyncClient, trunks: MemoryTrunks, routes: MemoryRoutes
) -> None:
    await brought(tenant_http)
    await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    admitted = set(trunks.trunks[A_RECORD.org].numbers)

    moved = await tenant_http.put(f"/v1/numbers/{ABAI}/env", json={"env": SANDBOX})

    assert moved.status_code == 200, moved.text
    assert (moved.json()["moved"], moved.json()["from"]) == (True, PRODUCTION)
    assert await routes.of_org(A_RECORD.org, PRODUCTION) == ()
    assert [route.number for route in await routes.of_org(A_RECORD.org, SANDBOX)] == [ABAI]
    assert trunks.trunks[A_RECORD.org].numbers == admitted, "the carrier is not touched by a move"
    back = await tenant_http.put(f"/v1/numbers/{ABAI}/env", json={"env": PRODUCTION})
    assert [route.number for route in await routes.of_org(A_RECORD.org, PRODUCTION)] == [ABAI]
    assert back.json()["moved"] is True


async def test_a_move_to_where_it_already_is_writes_nothing_and_says_so(
    tenant_http: httpx.AsyncClient,
) -> None:
    await brought(tenant_http)
    await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})

    again = await tenant_http.put(f"/v1/numbers/{ABAI}/env", json={"env": PRODUCTION})

    assert again.status_code == 200
    assert again.json()["moved"] is False
    assert again.json()["said"] == ALREADY_THERE.format(number=ABAI, env=PRODUCTION)


async def test_a_move_refuses_a_number_this_org_does_not_have_and_a_word_that_is_no_world(
    tenant_http: httpx.AsyncClient,
) -> None:
    nobodys = await tenant_http.put("/v1/numbers/+15550000000/env", json={"env": SANDBOX})
    assert nobodys.status_code == 404
    assert nobodys.json()["detail"] == NOT_THIS_ORGS.format(number="+15550000000")
    await brought(tenant_http)
    await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    staging = await tenant_http.put(f"/v1/numbers/{ABAI}/env", json={"env": "staging"})
    assert staging.status_code == 400 and "'staging'" in staging.json()["detail"]
