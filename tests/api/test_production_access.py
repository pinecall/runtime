"""The admin says who acts in production: at the invitation, later on the row, never an admin's."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.membership import AN_ADMIN_OPENS_PRODUCTION
from tests.api.test_members_and_login import MEMBERS, invited

pytestmark = pytest.mark.unit


async def test_an_invitation_says_whether_the_person_acts_in_production(
    tenant_http: httpx.AsyncClient,
) -> None:
    kept_out = await invited(tenant_http)
    assert kept_out["member"]["production"] is False
    let_in = await invited(tenant_http, email="diana@clinica.uy", production=True)
    assert let_in["member"]["production"] is True


async def test_the_switch_moves_on_the_row_and_the_listing_says_it(
    tenant_http: httpx.AsyncClient,
) -> None:
    member = (await invited(tenant_http))["member"]
    changed = await tenant_http.patch(f"{MEMBERS}/{member['id']}", json={"production": True})
    assert changed.status_code == 200, changed.text
    assert changed.json()["production"] is True
    listed = (await tenant_http.get(MEMBERS)).json()["members"]
    assert [row["production"] for row in listed] == [True]


async def test_an_admin_always_opens_production_and_taking_it_away_is_refused(
    tenant_http: httpx.AsyncClient,
) -> None:
    admin = (await invited(tenant_http, role="admin"))["member"]
    assert admin["production"] is True, "an admin opens production whatever the switch says"
    refused = await tenant_http.patch(f"{MEMBERS}/{admin['id']}", json={"production": False})
    assert refused.status_code == 409
    assert refused.json()["detail"] == AN_ADMIN_OPENS_PRODUCTION.format(email="berna@clinica.uy")
