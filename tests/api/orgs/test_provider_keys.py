"""/v1/ops/orgs/{org}/provider-keys and the one door that reads a key back: whose, and to whom."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.auth.keys import MemoryKeys
from pinecall.orgs.table import MemoryOrgs
from pinecall.orgs.vault import NO_VAULT_KEY, Vault
from pinecall.types import VENDORS
from tests.api.conftest import (
    A_DEV_KEY,
    A_KEY,
    A_LIVEKIT,
    A_RECORD,
    AGENT,
    AN_OPS_KEY,
    AN_ORG,
    a_door,
    an_app,
    got,
)
from tests.api.orgs.test_two_orgs_never_cross import (
    A_NUMBER,
    ANOTHER_AGENT,
    ANOTHER_KEY,
    ANOTHER_ORG,
    ANOTHER_RECORD,
    another_app,
    holding,
)

pytestmark = pytest.mark.unit

THE_ORGS_KEY = "sk-the-clinic-brought-its-own-elevenlabs-key"
OPS = f"/v1/ops/orgs/{AN_ORG.slug}/provider-keys"
THE_WORKERS_DOOR = f"/v1/agents/{AGENT}/provider-keys"


@pytest.fixture
def keys() -> MemoryKeys:
    """Two orgs knock at this gateway: the clinic, and the shop across the street."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_RECORD})


@pytest.fixture
def orgs() -> MemoryOrgs:
    """Both are tenants the operator created."""
    return MemoryOrgs([AN_ORG, ANOTHER_ORG])


async def kept(ops_http: httpx.AsyncClient, vendor: str = "elevenlabs") -> httpx.Response:
    """The clinic brings its own key for one vendor, through the operator's own door."""
    return await ops_http.put(f"{OPS}/{vendor}", json={"key": THE_ORGS_KEY})


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
    refused = await ops_http.put(f"{OPS}/11labs", json={"key": THE_ORGS_KEY})
    assert refused.status_code == 400
    assert all(vendor in refused.json()["detail"] for vendor in VENDORS)
    assert THE_ORGS_KEY not in refused.text


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


# ── the worker's door ───────────────────────────────────────────────────────────


async def test_the_worker_reads_its_own_orgs_keys_and_gets_nothing_when_it_brought_none(
    ops_http: httpx.AsyncClient, gateway: TestClient
) -> None:
    """Criterion 1 on the wire: the one response in the runtime that carries a provider key."""
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (200, {"keys": {}})
        assert (await kept(ops_http)).status_code == 204
        assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (
            200,
            {"keys": {"elevenlabs": THE_ORGS_KEY}},
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
        assert got(gateway, THE_WORKERS_DOOR, A_KEY)[1] == {"keys": {"elevenlabs": THE_ORGS_KEY}}
        shops = got(gateway, f"/v1/agents/{ANOTHER_AGENT}/provider-keys", ANOTHER_KEY)[1]
        assert shops == {"keys": {"soniox": "sk-the-shops"}}


# ── a box that was given no vault key ───────────────────────────────────────────


class TestARuntimeWithNoVaultKey:
    """Criterion 4: the doors say so, and every call still runs on the box's own vendor keys."""

    @pytest.fixture
    def settings(self) -> Settings:
        """The same environment the suite's gateway reads, with the vault key left unset."""
        return Settings(
            dev_key=A_DEV_KEY,
            ops_key=AN_OPS_KEY,
            livekit_api_key=A_LIVEKIT.api_key,
            livekit_api_secret=A_LIVEKIT.api_secret,
        )

    @pytest.fixture
    def vault(self) -> Vault | None:
        """What vault_for returns when PINECALL_VAULT_KEY is unset: nothing to keep a key in."""
        return None

    async def test_every_operator_door_is_503_and_says_which_variable(
        self, ops_http: httpx.AsyncClient
    ) -> None:
        refused = await kept(ops_http)
        assert refused.status_code == 503
        assert refused.json()["detail"] == NO_VAULT_KEY
        assert (await ops_http.get(OPS)).status_code == 503
        assert (await ops_http.delete(f"{OPS}/elevenlabs")).status_code == 503

    def test_the_worker_is_told_the_org_brought_none_and_the_call_goes_on(
        self, gateway: TestClient
    ) -> None:
        """A box with no vault is a whole install: every call runs on the keys of the box."""
        with an_app(gateway) as ours:
            holding(ours, AGENT)
            assert got(gateway, THE_WORKERS_DOOR, A_KEY) == (200, {"keys": {}})
