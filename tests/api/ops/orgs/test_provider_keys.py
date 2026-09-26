"""The provider-key doors — the tenant's, the operator's, the worker's: whose, and to whom."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.auth.keys import MemoryKeys
from pinecall.orgs.records import MemoryOrgs
from pinecall.orgs.vault import NO_VAULT_KEY
from pinecall.providers.catalog import vendors_with_a_key
from pinecall.types import ProviderKeys, Quotas
from tests.api.conftest import (
    A_KEY,
    A_LIVEKIT,
    A_RECORD,
    AGENT,
    AN_OPS_KEY,
    AN_ORG,
    over_the_asgi_app,
)
from tests.api.no_vault import WithNoVaultKey
from tests.api.ops.orgs.test_two_orgs_never_cross import (
    A_NUMBER,
    ANOTHER_AGENT,
    ANOTHER_KEY,
    ANOTHER_ORG,
    ANOTHER_RECORD,
    another_app,
    holding,
)
from tests.api.talking import a_call_the_app_ends, a_door, an_app, declared, got

pytestmark = pytest.mark.unit

THE_ORGS_KEY = "sk-the-clinic-brought-its-own-elevenlabs-key"
OPS = f"/v1/ops/orgs/{AN_ORG.slug}/provider-keys"
# The tenant's own, which names no org at all: the key that knocks IS the org.
TENANT = "/v1/provider-keys"
THE_WORKERS_DOOR = f"/v1/agents/{AGENT}/provider-keys"


@pytest.fixture
def keys() -> MemoryKeys:
    """Two orgs knock at this gateway: the clinic, and the shop across the street."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_RECORD})


@pytest.fixture
def orgs() -> MemoryOrgs:
    """Both are tenants the operator created."""
    return MemoryOrgs([AN_ORG, ANOTHER_ORG])


@pytest.fixture
async def shop_http(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """The shop's own terminal on this same gateway, knocking with the shop's own key."""
    http = over_the_asgi_app(f"Bearer {ANOTHER_KEY}")
    yield http
    await http.aclose()


async def kept(ops_http: httpx.AsyncClient, vendor: str = "elevenlabs") -> httpx.Response:
    """The clinic brings its own key for one vendor, through the operator's own door."""
    return await ops_http.put(f"{OPS}/{vendor}", json={"key": THE_ORGS_KEY})


async def brought(tenant_http: httpx.AsyncClient, vendor: str = "elevenlabs") -> httpx.Response:
    """The same key, brought by the tenant itself, with no operator anywhere in it."""
    return await tenant_http.put(f"{TENANT}/{vendor}", json={"key": THE_ORGS_KEY})


# ── the operator's three doors ──────────────────────────────────────────────────


async def test_a_key_is_kept_listed_by_name_and_dropped(ops_http: httpx.AsyncClient) -> None:
    """Criterion 3, the listing half: what comes back is which vendors, never any value."""
    assert (await ops_http.get(OPS)).json() == {"vendors": []}
    assert (await kept(ops_http)).status_code == 204
    listed = await ops_http.get(OPS)
    assert listed.json() == {"vendors": ["elevenlabs"]}
    assert THE_ORGS_KEY not in listed.text
    assert (await ops_http.delete(f"{OPS}/elevenlabs")).status_code == 204
    assert (await ops_http.get(OPS)).json() == {"vendors": []}


async def test_dropping_a_key_the_org_never_brought_is_404(ops_http: httpx.AsyncClient) -> None:
    """A typo in `provider-key rm` must never read as done, the rule every rm door follows."""
    refused = await ops_http.delete(f"{OPS}/soniox")
    assert refused.status_code == 404
    assert "has no soniox key" in refused.json()["detail"]


async def test_a_vendor_this_build_does_not_run_is_400_with_the_ones_it_does(
    ops_http: httpx.AsyncClient,
) -> None:
    """400 and not 422: the body was right and the word in the path is not one of ours."""
    refused = await ops_http.put(f"{OPS}/zenith", json={"key": THE_ORGS_KEY})
    assert refused.status_code == 400
    assert all(vendor in refused.json()["detail"] for vendor in vendors_with_a_key())
    assert THE_ORGS_KEY not in refused.text


# `11labs` was the word in the sentence this door's refusal was written around, and for a year it
# WAS a refusal. It is an alias now (providers/catalog.py), and the point of resolving one here is
# that a tenant who brought a key under one spelling reads it back under the other: two spellings
# and one row, or the vault answers "you brought none" to somebody who plainly did.
async def test_a_vendor_brought_under_an_alias_is_one_row_under_its_own_name(
    ops_http: httpx.AsyncClient,
) -> None:
    assert (await ops_http.put(f"{OPS}/11labs", json={"key": THE_ORGS_KEY})).status_code == 204
    assert (await ops_http.get(OPS)).json()["vendors"] == ["elevenlabs"]
    assert (await ops_http.delete(f"{OPS}/elevenlabs")).status_code == 204


async def test_an_org_nobody_typed_is_404_on_every_provider_key_door(
    ops_http: httpx.AsyncClient,
) -> None:
    nobody = "/v1/ops/orgs/nobody/provider-keys"
    assert (await ops_http.get(nobody)).status_code == 404
    assert (await ops_http.put(f"{nobody}/soniox", json={"key": "x"})).status_code == 404
    assert (await ops_http.delete(f"{nobody}/soniox")).status_code == 404


async def test_an_orgs_own_api_key_opens_none_of_the_operators_doors(gateway: TestClient) -> None:
    """Criterion 3, the second half: /v1/ops is the box's, and a tenant's key is not the box's."""
    # starlette's TestClient is an httpx client and httpx 0.x ships no stubs for these members;
    # the package's own `got` does the same at every other GET in this suite.
    handle: Any = gateway
    theirs = {"Authorization": f"Bearer {A_KEY}"}
    assert handle.get(OPS, headers=theirs).status_code == 401
    assert handle.put(f"{OPS}/soniox", json={"key": "x"}, headers=theirs).status_code == 401
    assert handle.delete(f"{OPS}/soniox", headers=theirs).status_code == 401


# ── the tenant's own three doors ────────────────────────────────────────────────


async def test_a_tenant_brings_a_key_lists_the_vendor_and_takes_it_back(
    tenant_http: httpx.AsyncClient,
) -> None:
    """Hosted is BYOK-first: a tenant holding its own API key needs no operator to bring a key."""
    assert (await tenant_http.get(TENANT)).json() == {"vendors": []}
    assert (await brought(tenant_http)).status_code == 204
    listed = await tenant_http.get(TENANT)
    assert listed.json() == {"vendors": ["elevenlabs"]}
    assert THE_ORGS_KEY not in listed.text
    assert (await tenant_http.delete(f"{TENANT}/elevenlabs")).status_code == 204
    assert (await tenant_http.get(TENANT)).json() == {"vendors": []}


async def test_the_listing_is_names_alone_in_the_order_a_person_reads_them(
    tenant_http: httpx.AsyncClient,
) -> None:
    """Sorted, because a listing whose order is the insert order is a listing nobody can scan."""
    for vendor in ("soniox", "anthropic", "elevenlabs"):
        assert (await brought(tenant_http, vendor)).status_code == 204
    assert (await tenant_http.get(TENANT)).json() == {
        "vendors": ["anthropic", "elevenlabs", "soniox"]
    }


async def test_a_second_org_never_sees_the_first_ones_row_and_cannot_reach_it(
    tenant_http: httpx.AsyncClient, shop_http: httpx.AsyncClient
) -> None:
    """There is no way to name another org here, so there is no way into another org's vault."""
    assert (await brought(tenant_http)).status_code == 204
    theirs = await shop_http.get(TENANT)
    assert theirs.json() == {"vendors": []}
    assert THE_ORGS_KEY not in theirs.text
    assert (await shop_http.delete(f"{TENANT}/elevenlabs")).status_code == 404
    assert (await tenant_http.get(TENANT)).json() == {"vendors": ["elevenlabs"]}


async def test_taking_back_a_vendor_this_org_never_brought_is_404(
    tenant_http: httpx.AsyncClient,
) -> None:
    """The same sentence the operator's door answers: a typo must never read as done."""
    refused = await tenant_http.delete(f"{TENANT}/soniox")
    assert refused.status_code == 404
    assert "has no soniox key" in refused.json()["detail"]


async def test_a_vendor_this_build_does_not_run_is_refused_by_name_at_the_tenants_door(
    tenant_http: httpx.AsyncClient,
) -> None:
    """One `_a_known_vendor`, so the tenant is told what to type in the operator's own words."""
    refused = await tenant_http.put(f"{TENANT}/zenith", json={"key": THE_ORGS_KEY})
    assert refused.status_code == 400
    assert all(vendor in refused.json()["detail"] for vendor in vendors_with_a_key())
    assert THE_ORGS_KEY not in refused.text
    assert (await tenant_http.delete(f"{TENANT}/zenith")).status_code == 400


def test_the_tenants_doors_take_an_api_key_and_the_boxs_ops_key_is_not_one(
    gateway: TestClient,
) -> None:
    """The mirror of the rule above it: /v1/ops is the box's, and this door is the tenant's."""
    handle: Any = gateway
    boxs = {"Authorization": f"Bearer {AN_OPS_KEY}"}
    assert handle.get(TENANT, headers=boxs).status_code == 401
    assert handle.put(f"{TENANT}/soniox", json={"key": "x"}, headers=boxs).status_code == 401
    assert handle.delete(f"{TENANT}/soniox", headers=boxs).status_code == 401
    assert handle.get(TENANT).status_code == 401


async def test_a_call_of_that_org_then_runs_on_the_key_the_tenant_brought_itself(
    tenant_http: httpx.AsyncClient, gateway: TestClient, keys_asked: list[ProviderKeys]
) -> None:
    """The whole point of the door: no operator was in it, and the next call is on that account."""
    assert (await brought(tenant_http, "anthropic")).status_code == 204
    with an_app(gateway) as ours:
        declared(ours)
        a_call_the_app_ends(gateway, ours)
    assert keys_asked[-1] == {"anthropic": THE_ORGS_KEY}


# ── the worker's door ───────────────────────────────────────────────────────────


async def test_the_worker_reads_its_own_orgs_keys_and_gets_nothing_when_it_brought_none(
    ops_http: httpx.AsyncClient, gateway: TestClient
) -> None:
    """Criterion 1 on the wire: the one response in the runtime that carries a provider key."""
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (200, {"keys": {}, "lends": None})
        assert (await kept(ops_http)).status_code == 204
        assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (
            200,
            {"keys": {"elevenlabs": THE_ORGS_KEY}, "lends": None},
        )


async def test_the_worker_is_told_what_the_box_lends_the_org_beside_its_keys(
    gateway: TestClient, orgs: MemoryOrgs
) -> None:
    """The lending rides the one door the worker reads keys at: one read, both halves."""
    await orgs.set_quotas(AN_ORG.id, Quotas(lends=frozenset({"deepgram", "cartesia"})))
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (
            200,
            {"keys": {}, "lends": ["cartesia", "deepgram"]},
        )


async def test_another_orgs_key_never_opens_this_orgs_provider_keys(
    ops_http: httpx.AsyncClient, gateway: TestClient
) -> None:
    """Criterion 3: a slug another org holds is a 404 here, the same sentence /config answers."""
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        assert (await kept(ops_http)).status_code == 204
        status, body = got(gateway, THE_WORKERS_DOOR, ANOTHER_KEY)
        assert status == 404
        assert THE_ORGS_KEY not in str(body)


async def test_two_orgs_on_one_gateway_each_read_their_own_row(
    ops_http: httpx.AsyncClient, gateway: TestClient
) -> None:
    """One box, two tenants, two vaults' worth of rows and no way from one into the other."""
    theirs = f"/v1/ops/orgs/{ANOTHER_ORG.slug}/provider-keys"
    assert (await kept(ops_http)).status_code == 204
    assert (await ops_http.put(f"{theirs}/soniox", json={"key": "sk-the-shops"})).status_code == 204
    with an_app(gateway) as ours, another_app(gateway) as shop:
        holding(ours, AGENT)
        holding(shop, ANOTHER_AGENT, a_door("phone", A_NUMBER))
        assert got(gateway, THE_WORKERS_DOOR, A_KEY)[1] == {
            "keys": {"elevenlabs": THE_ORGS_KEY},
            "lends": None,
        }
        shops = got(gateway, f"/v1/agents/{ANOTHER_AGENT}/provider-keys", ANOTHER_KEY)[1]
        assert shops == {"keys": {"soniox": "sk-the-shops"}, "lends": None}


# ── a box that was given no vault key ───────────────────────────────────────────


class TestARuntimeWithNoVaultKey(WithNoVaultKey):
    """Criterion 4: the doors say so, and every call still runs on the box's own vendor keys."""

    door = TENANT

    @pytest.fixture
    def settings(self) -> Settings:
        """The same environment the suite's gateway reads, with the vault key left unset."""
        return Settings(
            world="production",
            ops_key=AN_OPS_KEY,
            livekit_api_key=A_LIVEKIT.api_key,
            livekit_api_secret=A_LIVEKIT.api_secret,
        )

    async def test_every_operator_door_is_503_and_says_which_variable(
        self, ops_http: httpx.AsyncClient
    ) -> None:
        refused = await kept(ops_http)
        assert refused.status_code == 503
        assert refused.json()["detail"] == NO_VAULT_KEY
        assert (await ops_http.get(OPS)).status_code == 503
        assert (await ops_http.delete(f"{OPS}/elevenlabs")).status_code == 503

    async def test_every_tenant_door_is_503_and_says_the_same_thing(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        """A tenant is told what the operator is told: this box cannot keep anybody's key."""
        refused = await brought(tenant_http)
        assert refused.status_code == 503
        assert refused.json()["detail"] == NO_VAULT_KEY
        assert (await tenant_http.delete(f"{TENANT}/elevenlabs")).status_code == 503

    def test_the_worker_is_told_the_org_brought_none_and_the_call_goes_on(
        self, gateway: TestClient
    ) -> None:
        """A box with no vault is a whole install: every call runs on the keys of the box."""
        with an_app(gateway) as ours:
            holding(ours, AGENT)
            assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (200, {"keys": {}, "lends": None})
