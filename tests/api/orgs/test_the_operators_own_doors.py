"""What the operator's page reads before anything else: that its key opens, and an org's people."""

from __future__ import annotations

import httpx
import pytest

from pinecall._version import __version__
from tests.api.conftest import AN_ORG

pytestmark = pytest.mark.unit

WHOAMI = "/v1/ops/whoami"
MEMBERS = f"/v1/ops/orgs/{AN_ORG.slug}/members"
A_PERSON = {"email": "ana@clinica.uy", "name": "Ana", "role": "manager"}


async def test_the_box_says_it_is_the_box_and_which_one(ops_http: httpx.AsyncClient) -> None:
    """The page proves its key here, as the console proves a person's at /v1/whoami."""
    answer = await ops_http.get(WHOAMI)
    assert answer.status_code == 200, answer.text
    said = answer.json()
    assert said["operator"] is True
    assert said["version"] == __version__
    # The suite's settings name no domain, and the page then says the URL it was loaded from.
    assert said["domain"] is None


async def test_a_key_that_is_not_the_boxs_opens_nothing_here(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """An org's own key is not the box's, however many scopes it holds."""
    assert (await tenant_http.get(WHOAMI)).status_code == 401
    assert (await stranger.get(WHOAMI)).status_code == 401


async def test_the_operator_reads_an_orgs_people_and_how_many_hold_a_seat(
    ops_http: httpx.AsyncClient, tenant_http: httpx.AsyncClient
) -> None:
    empty = await ops_http.get(MEMBERS)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"members": [], "seated": 0}

    invited = await tenant_http.post("/v1/members", json=A_PERSON)
    assert invited.status_code == 201, invited.text
    listed = (await ops_http.get(MEMBERS)).json()
    assert [one["email"] for one in listed["members"]] == ["ana@clinica.uy"]
    assert listed["members"][0]["role"] == "manager"
    assert listed["seated"] == 1, "an invitation takes a seat before it is accepted"
    assert invited.json()["token"] not in str(listed), "a token is answered once and never listed"


async def test_a_disabled_member_is_still_a_row_and_holds_no_seat(
    ops_http: httpx.AsyncClient, tenant_http: httpx.AsyncClient
) -> None:
    invited = await tenant_http.post("/v1/members", json=A_PERSON)
    who = str(invited.json()["member"]["id"])
    await tenant_http.patch(f"/v1/members/{who}", json={"status": "disabled"})
    listed = (await ops_http.get(MEMBERS)).json()
    assert len(listed["members"]) == 1 and listed["members"][0]["status"] == "disabled"
    assert listed["seated"] == 0


async def test_an_org_nobody_made_is_a_404_and_not_an_empty_list(
    ops_http: httpx.AsyncClient,
) -> None:
    assert (await ops_http.get("/v1/ops/orgs/nobody/members")).status_code == 404
