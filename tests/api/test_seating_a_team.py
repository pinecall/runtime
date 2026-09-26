"""The seats a plan sells: an invitation takes one, a disabling frees one, a re-invite none."""

from __future__ import annotations

import httpx
import pytest

from pinecall.orgs.records import MemoryOrgs
from pinecall.types import Quotas
from tests.api.conftest import AN_ORG

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"

# Three seats, which is what a free plan sells: whoever signed up, and two people beside them.
THREE = 3


@pytest.fixture
async def orgs() -> MemoryOrgs:
    """The org the tenant's key names, held to three seats before anybody is invited."""
    kept = MemoryOrgs([AN_ORG])
    await kept.set_quotas(AN_ORG.id, Quotas(seats=THREE))
    return kept


async def invited(tenant: httpx.AsyncClient, who: str) -> httpx.Response:
    """One person invited by email, with the least a member needs to be a row."""
    said = {"email": f"{who}@clinica.uy", "name": who.title(), "role": "developer"}
    return await tenant.post(MEMBERS, json=said)


async def emails(tenant: httpx.AsyncClient) -> list[str]:
    """Who the org has rows for, disabled ones included."""
    listing = await tenant.get(MEMBERS)
    return [str(one["email"]) for one in listing.json()["members"]]


async def a_full_org(tenant: httpx.AsyncClient) -> None:
    """Three people invited, which is every seat this org has."""
    for who in ("ana", "berna", "carla"):
        assert (await invited(tenant, who)).status_code == 201, who


async def test_the_org_seats_its_plans_worth_and_the_next_invitation_is_refused(
    tenant_http: httpx.AsyncClient,
) -> None:
    await a_full_org(tenant_http)
    refused = await invited(tenant_http, "diego")
    assert refused.status_code == 429
    detail = str(refused.json()["detail"])
    assert "has used 3 of its 3 seats" in detail, detail
    assert await emails(tenant_http) == [
        "ana@clinica.uy",
        "berna@clinica.uy",
        "carla@clinica.uy",
    ], "the refused invitation left no row"


async def test_an_invitation_takes_the_seat_before_anybody_accepts_it(
    tenant_http: httpx.AsyncClient,
) -> None:
    """Or an org at its limit could invite forever and seat them all the moment they accepted."""
    await a_full_org(tenant_http)
    listing = (await tenant_http.get(MEMBERS)).json()["members"]
    assert all(one["status"] == "invited" for one in listing)
    assert (await invited(tenant_http, "diego")).status_code == 429


async def test_re_inviting_somebody_who_holds_a_seat_takes_no_second_one(
    tenant_http: httpx.AsyncClient,
) -> None:
    """A token dies in a week; an org at its limit must still be able to send a new one."""
    await a_full_org(tenant_http)
    again = await invited(tenant_http, "carla")
    assert again.status_code == 201
    assert str(again.json()["token"]).startswith("inv_")
    assert len(await emails(tenant_http)) == THREE, "a second token, not a second row"


async def test_disabling_somebody_frees_their_seat_and_keeps_their_row(
    tenant_http: httpx.AsyncClient,
) -> None:
    """The row stays because the log names them; the seat does not, which is how one is freed."""
    berna = str((await invited(tenant_http, "berna")).json()["member"]["id"])
    for who in ("ana", "carla"):
        await invited(tenant_http, who)
    assert (await invited(tenant_http, "diego")).status_code == 429
    off = await tenant_http.patch(f"{MEMBERS}/{berna}", json={"status": "disabled"})
    assert off.status_code == 200, off.text
    assert (await invited(tenant_http, "diego")).status_code == 201
    assert len(await emails(tenant_http)) == 4, "four rows, three seats"


class TestAnOrgNobodyLimited:
    """A box of its own: no quotas row, so nobody is ever counted and nobody is ever refused."""

    @pytest.fixture
    def orgs(self) -> MemoryOrgs:
        return MemoryOrgs([AN_ORG])

    async def test_it_seats_everybody(self, tenant_http: httpx.AsyncClient) -> None:
        for who in ("ana", "berna", "carla", "diego", "sol"):
            assert (await invited(tenant_http, who)).status_code == 201, who
