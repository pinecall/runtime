"""The org's SSO configuration: who may set it, what a reader sees, and what is never answered."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.sso import CALLBACK, NO_SSO
from pinecall.orgs.sso import Sso
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Org
from tests.api.conftest import AN_OPS_KEY, AN_ORG
from tests.api.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER
from tests.api.no_vault import WithNoVaultKey

pytestmark = pytest.mark.unit

THE_DOOR = "/v1/org/sso"
ANOTHER_ORG = Org(id="tienda", slug="tienda-sur", name="Tienda Sur")

WIRED = {
    "issuer": ISSUER,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "domains": ["TiendaSur.uy", "@clinica.test"],
    "role": "developer",
}


@pytest.fixture(autouse=True)
def wiring_a_provider(sso: Sso | None, http: httpx.AsyncClient) -> None:
    """The table these doors write, and the client the PUT checks an issuer answers with."""


async def test_an_unwired_org_says_so_and_names_the_uri_to_register(
    tenant_http: httpx.AsyncClient,
) -> None:
    """The redirect URI is in the answer before anything is wired: it is what an admin pastes."""
    answer = await tenant_http.get(THE_DOOR)
    assert answer.status_code == 200
    # One shape either way, so a page parses one envelope: the empty value of every field.
    assert answer.json() == {
        "configured": False,
        "issuer": None,
        "client_id": None,
        "domains": [],
        "role": None,
        "required": False,
        "redirect_uri": f"http://gateway.test{CALLBACK}",
    }


async def test_wiring_keeps_the_domains_folded_and_never_answers_the_secret(
    tenant_http: httpx.AsyncClient,
    sso: Sso,
) -> None:
    """A domain typed with a capital or an `@` is the domain an address folds to."""
    answer = await tenant_http.put(THE_DOOR, json=WIRED)
    assert answer.status_code == 200
    said = answer.json()
    assert said["domains"] == ["tiendasur.uy", "clinica.test"]
    assert said["configured"] is True and said["required"] is False
    assert CLIENT_SECRET not in answer.text
    # …and it is not readable back through the door either, on this key or any other.
    read = await tenant_http.get(THE_DOOR)
    assert CLIENT_SECRET not in read.text and read.json()["client_id"] == CLIENT_ID
    kept = await sso.of(AN_ORG.id)
    assert kept is not None and kept.client_secret == CLIENT_SECRET


async def test_an_issuer_nobody_answers_at_is_refused_and_nothing_is_kept(
    tenant_http: httpx.AsyncClient, sso: Sso
) -> None:
    """The one check this door makes over the network, so a typo is found in the console."""
    answer = await tenant_http.put(THE_DOOR, json={**WIRED, "issuer": "https://nobody.test"})
    assert answer.status_code == 400
    assert "nothing was kept" in answer.json()["detail"]
    assert await sso.of(AN_ORG.id) is None


async def test_an_issuer_that_is_not_https_never_reaches_the_network(
    tenant_http: httpx.AsyncClient,
) -> None:
    answer = await tenant_http.put(THE_DOOR, json={**WIRED, "issuer": "http://idp.test"})
    assert answer.status_code == 400 and "https" in answer.json()["detail"]


async def test_a_role_nobody_has_is_refused_with_the_five_that_exist(
    tenant_http: httpx.AsyncClient,
) -> None:
    answer = await tenant_http.put(THE_DOOR, json={**WIRED, "role": "boss"})
    assert answer.status_code == 400 and "'boss'" in answer.json()["detail"]


async def test_unwiring_twice_is_a_404_the_second_time(tenant_http: httpx.AsyncClient) -> None:
    """`orgs sso rm` on an org that has none must never read as done."""
    await tenant_http.put(THE_DOOR, json=WIRED)
    assert (await tenant_http.delete(THE_DOOR)).status_code == 204
    gone = await tenant_http.delete(THE_DOOR)
    assert gone.status_code == 404 and gone.json()["detail"] == NO_SSO


# ── the other org, and the box ──────────────────────────────────────────────────


@pytest.fixture
def orgs() -> MemoryOrgs:
    """Two tenants, so what one wires says nothing about the other."""
    return MemoryOrgs([AN_ORG, ANOTHER_ORG])


async def test_one_orgs_provider_is_not_anothers(
    tenant_http: httpx.AsyncClient, ops_http: httpx.AsyncClient
) -> None:
    """The tenant door names no org — its key is one — so the neighbour reads as unwired."""
    await tenant_http.put(THE_DOOR, json=WIRED)
    theirs = await ops_http.get(f"/v1/ops/orgs/{ANOTHER_ORG.slug}/sso")
    assert theirs.status_code == 200 and theirs.json()["configured"] is False
    ours = await ops_http.get(f"/v1/ops/orgs/{AN_ORG.slug}/sso")
    assert ours.json()["issuer"] == ISSUER and CLIENT_SECRET not in ours.text


async def test_the_operator_turns_required_off_and_may_not_turn_it_on(
    tenant_http: httpx.AsyncClient, ops_http: httpx.AsyncClient, sso: Sso
) -> None:
    """The break-glass: the box lets a locked-out org back in, and decides nothing else."""
    await tenant_http.put(THE_DOOR, json={**WIRED, "required": True})
    door = f"/v1/ops/orgs/{AN_ORG.slug}/sso/required"
    off = await ops_http.put(door, json={"required": False})
    assert off.status_code == 200 and off.json()["required"] is False
    kept = await sso.of(AN_ORG.id)
    # Everything else the org wired is untouched, the client secret included.
    assert kept is not None and kept.client_secret == CLIENT_SECRET and kept.role == "developer"
    unwired = await ops_http.put(
        f"/v1/ops/orgs/{ANOTHER_ORG.slug}/sso/required", json={"required": False}
    )
    assert unwired.status_code == 404


async def test_the_ops_doors_take_the_box_key_and_no_tenants(
    tenant_http: httpx.AsyncClient,
) -> None:
    """A tenant's own key opens no /v1/ops door, whatever it opens in its org."""
    answer = await tenant_http.get(f"/v1/ops/orgs/{AN_ORG.slug}/sso")
    assert answer.status_code in (401, 403)
    assert AN_OPS_KEY not in answer.text


# ── a box with no vault key ─────────────────────────────────────────────────────


class TestWithNoVaultKey(WithNoVaultKey):
    """A runtime given no PINECALL_VAULT_KEY keeps nobody's secret, and says so in one sentence."""

    door = THE_DOOR

    @pytest.fixture
    def sso(self) -> Sso | None:
        return None

    async def test_the_discovery_answers_nobody_rather_than_refusing(
        self, stranger: httpx.AsyncClient
    ) -> None:
        """A sign-in page asking whether this address has a provider gets the truth: none."""
        answer = await stranger.post("/v1/login/sso/discover", json={"email": "nico@tiendasur.uy"})
        assert answer.status_code == 200 and answer.json() == {"orgs": []}
