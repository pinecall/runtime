"""A number the box buys for the org: the plan, the purchase, the quota that caps them."""

from __future__ import annotations

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.managed import NO_BOX_CARRIER, NONE_FOR_SALE
from pinecall.orgs.table import MemoryOrgs
from pinecall.routes.table import MemoryRoutes
from pinecall.routes.trunks import MemoryTrunks
from pinecall.routes.twilio import TWILIO_SIGNALLING
from pinecall.types import PRODUCTION, Quotas
from tests.api.carriers import A_KEY_SID, A_SID, FakeTwilio
from tests.api.conftest import A_LIVEKIT, A_RECORD, A_VAULT_KEY, AGENT, AN_OPS_KEY
from tests.api.test_numbers import ABAI, brought

pytestmark = pytest.mark.unit

BUY = "/v1/numbers/buy"
SPRINGFIELD = {"country": "US", "area_code": "417", "agent": AGENT}
A_NEW_ONE = "+14175550100"
ANOTHER = "+14175550101"


@pytest.fixture
def settings() -> Settings:
    """A box with a name and a Twilio account of its own: what a purchase is billed to."""
    return Settings(
        ops_key=AN_OPS_KEY,
        vault_key=A_VAULT_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        domain="box.pinecall.io",
        twilio_account_sid=A_SID,
        twilio_api_key=A_KEY_SID,
        twilio_api_secret="the-boxs-secret",
    )


@pytest.fixture
def twilio_account(twilio_account: FakeTwilio) -> FakeTwilio:
    """The same fake, with two Springfield numbers for sale."""
    twilio_account.shelf["US 417"] = [A_NEW_ONE, ANOTHER]
    return twilio_account


async def test_a_dry_run_names_the_number_it_would_buy_and_buys_nothing(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    trunks: MemoryTrunks,
    routes: MemoryRoutes,
) -> None:
    answer = await tenant_http.post(f"{BUY}?dry_run=true", json=SPRINGFIELD)
    assert answer.status_code == 200, answer.text
    said = answer.json()
    assert said["dry_run"] is True and said["route"]["managed"] is True
    assert said["steps"] == [
        f"buy      {A_NEW_ONE} — on account {A_SID}, billed to the box",
        f"trunk    pinecall — created on account {A_SID}",
        "origin   sip:box.pinecall.io:5060;transport=udp — set",
        f"number   {A_NEW_ONE} — attached to the trunk",
        f"livekit  inbound trunk pinecall-{A_RECORD.org}: +{A_NEW_ONE}, from "
        f"{len(TWILIO_SIGNALLING)} networks; one room per caller",
        f"route    {A_NEW_ONE} phone → {AGENT} in production",
    ]
    assert twilio_account.made == [] and trunks.trunks == {}
    assert await routes.of_org(A_RECORD.org, PRODUCTION) == ()


async def test_a_purchase_buys_attaches_admits_and_routes_the_number_as_managed(
    tenant_http: httpx.AsyncClient,
    twilio_account: FakeTwilio,
    trunks: MemoryTrunks,
    routes: MemoryRoutes,
) -> None:
    answer = await tenant_http.post(BUY, json=SPRINGFIELD)
    assert answer.status_code == 200, answer.text
    assert twilio_account.made == [
        f"buy {A_NEW_ONE}",
        "trunk pinecall",
        "origin sip:box.pinecall.io:5060;transport=udp",
        f"attach {A_NEW_ONE}",
    ]
    assert trunks.trunks[A_RECORD.org].numbers == {A_NEW_ONE}
    (route,) = await routes.of_org(A_RECORD.org, PRODUCTION)
    assert (route.number, route.agent, route.managed) == (A_NEW_ONE, AGENT, True)
    assert await routes.managed_by(A_RECORD.org) == 1
    listed = (await tenant_http.get("/v1/numbers")).json()
    assert [(door["route"]["number"], door["route"]["managed"]) for door in listed] == [
        (A_NEW_ONE, True)
    ]


async def test_the_plans_stock_of_numbers_caps_purchases_and_never_imports(
    tenant_http: httpx.AsyncClient, orgs: MemoryOrgs, twilio_account: FakeTwilio
) -> None:
    await orgs.set_quotas(A_RECORD.org, Quotas(numbers=1))
    # The org's own number, imported from its own account: not the box's to count.
    await brought(tenant_http)
    imported = await tenant_http.post("/v1/numbers", json={"number": ABAI, "agent": AGENT})
    assert imported.status_code == 200, imported.text
    first = await tenant_http.post(BUY, json=SPRINGFIELD)
    assert first.status_code == 200, first.text
    second = await tenant_http.post(BUY, json=SPRINGFIELD)
    assert second.status_code == 429
    assert second.json()["detail"] == (
        f"org {A_RECORD.org} has used 1 of its 1 numbers: credits.exhausted"
    )
    assert sum(one.startswith("buy ") for one in twilio_account.made) == 1
    # Letting the bought one go makes room again.
    assert (await tenant_http.delete(f"/v1/numbers/{A_NEW_ONE}")).status_code == 204
    third = await tenant_http.post(BUY, json=SPRINGFIELD)
    assert third.status_code == 200 and third.json()["route"]["number"] == ANOTHER


async def test_a_plan_with_no_numbers_at_all_is_refused_before_twilio_is_asked(
    tenant_http: httpx.AsyncClient, orgs: MemoryOrgs, twilio_account: FakeTwilio
) -> None:
    await orgs.set_quotas(A_RECORD.org, Quotas(numbers=0))
    answer = await tenant_http.post(BUY, json=SPRINGFIELD)
    assert answer.status_code == 429 and twilio_account.made == []


async def test_nothing_for_sale_there_is_a_404_in_twilios_absence(
    tenant_http: httpx.AsyncClient,
) -> None:
    answer = await tenant_http.post(BUY, json={**SPRINGFIELD, "area_code": "999"})
    assert answer.status_code == 404
    assert answer.json()["detail"] == NONE_FOR_SALE.format(where="US 999")


async def test_a_box_with_no_twilio_of_its_own_buys_for_nobody(
    tenant_http: httpx.AsyncClient, settings: Settings
) -> None:
    from pinecall.api import _deps
    from pinecall.api.app import app

    poor = settings.model_copy(update={"twilio_account_sid": None})
    app.dependency_overrides[_deps.a_settings] = lambda: poor
    answer = await tenant_http.post(BUY, json=SPRINGFIELD)
    assert (answer.status_code, answer.json()["detail"]) == (503, NO_BOX_CARRIER)
