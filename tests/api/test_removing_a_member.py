"""A member removed for good: their keys stop, their links die, the seat is free, the log reads."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.members import NO_SUCH_MEMBER, NOT_YOURSELF, THE_LAST_ADMIN
from pinecall.auth.keys import MemoryKeys
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Quotas
from tests.api.conftest import AN_ORG, over_the_asgi_app

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"
OPS_MEMBERS = f"/v1/ops/orgs/{AN_ORG.slug}/members"
A_PASSWORD = "correct horse battery staple"


async def invited(tenant: httpx.AsyncClient, who: str, role: str = "developer") -> dict[str, Any]:
    said = {"email": f"{who}@clinica.uy", "name": who.title(), "role": role}
    answer = await tenant.post(MEMBERS, json=said)
    assert answer.status_code == 201, answer.text
    return answer.json()


async def seated(
    tenant: httpx.AsyncClient, stranger: httpx.AsyncClient, who: str, role: str = "developer"
) -> dict[str, Any]:
    """Somebody invited who then chose a password: the member, and the first key of theirs."""
    token = (await invited(tenant, who, role))["token"]
    answer = await stranger.post(f"/v1/invitations/{token}", json={"password": A_PASSWORD})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_removing_a_member_stops_their_keys_forgets_the_row_and_refuses_their_login(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    ana = await seated(tenant_http, stranger, "ana")
    theirs = over_the_asgi_app(f"Bearer {ana['key']}")
    assert (await theirs.get("/v1/whoami")).status_code == 200

    gone = await tenant_http.delete(f"{MEMBERS}/{ana['member']['id']}")

    assert (gone.status_code, gone.content) == (204, b"")
    assert (await theirs.get("/v1/whoami")).status_code == 401, "the key stopped with the row"
    assert (await tenant_http.get(MEMBERS)).json()["members"] == []
    login = {"org": AN_ORG.slug, "email": "ana@clinica.uy", "password": A_PASSWORD}
    assert (await stranger.post("/v1/login", json=login)).status_code == 401
    # The key's row stays, revoked and still naming the id: what the log wrote stays readable.
    kept = [row for row in await keys.listed(AN_ORG.id) if row.subject == ana["member"]["id"]]
    assert kept and all(row.revoked_at is not None for row in kept)
    await theirs.aclose()


async def test_the_link_of_somebody_removed_while_still_invited_opens_nothing(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    berna = await invited(tenant_http, "berna")
    assert (await tenant_http.delete(f"{MEMBERS}/{berna['member']['id']}")).status_code == 204
    spent = await stranger.post(f"/v1/invitations/{berna['token']}", json={"password": A_PASSWORD})
    assert spent.status_code == 404


async def test_removing_frees_the_seat_and_the_same_address_can_be_invited_again(
    tenant_http: httpx.AsyncClient, orgs: MemoryOrgs
) -> None:
    await orgs.set_quotas(AN_ORG.id, Quotas(seats=1))
    ana = await invited(tenant_http, "ana")
    full = await tenant_http.post(
        MEMBERS, json={"email": "b@clinica.uy", "name": "B", "role": "qa"}
    )
    assert full.status_code == 429
    assert (await tenant_http.delete(f"{MEMBERS}/{ana['member']['id']}")).status_code == 204
    again = await invited(tenant_http, "ana")
    assert again["member"]["id"] != ana["member"]["id"], "a new row: the old one is gone for good"


async def test_nobody_removes_themselves_and_the_last_active_admin_stays(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    ana = await seated(tenant_http, stranger, "ana", "admin")
    hers = over_the_asgi_app(f"Bearer {ana['key']}")
    me = ana["member"]["id"]

    myself = await hers.delete(f"{MEMBERS}/{me}")
    assert (myself.status_code, myself.json()["detail"]) == (409, NOT_YOURSELF)

    # An admin still invited runs nothing yet, so Ana is the last one who can.
    await invited(tenant_http, "berna", "admin")
    last = await tenant_http.delete(f"{MEMBERS}/{me}")
    assert last.status_code == 409
    assert last.json()["detail"] == THE_LAST_ADMIN.format(email="ana@clinica.uy")

    carla = await seated(tenant_http, stranger, "carla", "admin")
    assert (await hers.delete(f"{MEMBERS}/{carla['member']['id']}")).status_code == 204
    await hers.aclose()


async def test_an_id_that_is_not_this_orgs_is_404_at_both_doors(
    tenant_http: httpx.AsyncClient, ops_http: httpx.AsyncClient
) -> None:
    for door in (
        tenant_http.delete(f"{MEMBERS}/m_nobody"),
        ops_http.delete(f"{OPS_MEMBERS}/m_nobody"),
    ):
        answer = await door
        assert (answer.status_code, answer.json()["detail"]) == (
            404,
            NO_SUCH_MEMBER.format(id="m_nobody"),
        )
    assert (await ops_http.delete("/v1/ops/orgs/nobody/members/m_1")).status_code == 404


async def test_the_operators_twin_removes_under_the_same_rules_less_yourself(
    tenant_http: httpx.AsyncClient, ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    ana = await seated(tenant_http, stranger, "ana", "admin")
    berna = await seated(tenant_http, stranger, "berna")
    theirs = over_the_asgi_app(f"Bearer {berna['key']}")

    assert (await ops_http.delete(f"{OPS_MEMBERS}/{berna['member']['id']}")).status_code == 204
    assert (await theirs.get("/v1/whoami")).status_code == 401
    last = await ops_http.delete(f"{OPS_MEMBERS}/{ana['member']['id']}")
    assert last.status_code == 409, "an org nobody can run is as broken when the box made it so"
    await theirs.aclose()


async def test_a_key_without_team_removes_nobody(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    ana = await seated(tenant_http, stranger, "ana", "qa")
    berna = await invited(tenant_http, "berna")
    hers = over_the_asgi_app(f"Bearer {ana['key']}")
    assert (await hers.delete(f"{MEMBERS}/{berna['member']['id']}")).status_code == 403
    await hers.aclose()
