"""/v1/ops/orgs against the real app: a tenant created, found by either name, limited, removed."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.auth.keys import fingerprint
from pinecall.routes.table import MemoryRoutes
from pinecall.types import Route
from tests.api.conftest import AN_ORG

pytestmark = pytest.mark.unit

ORGS = "/v1/ops/orgs"


async def added(ops_http: httpx.AsyncClient, slug: str, name: str | None = None) -> dict[str, Any]:
    answer = await ops_http.post(ORGS, json={"slug": slug, "name": name})
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def test_a_new_org_gets_a_minted_id_and_is_found_by_id_and_by_slug(
    ops_http: httpx.AsyncClient,
) -> None:
    org = await added(ops_http, "tienda-sur", "Tienda Sur")
    assert org["id"].startswith("org_") and org["slug"] == "tienda-sur"
    by_id = (await ops_http.get(f"{ORGS}/{org['id']}")).json()
    by_slug = (await ops_http.get(f"{ORGS}/tienda-sur")).json()
    assert by_id == by_slug
    assert by_id["quotas"] == {
        "minutes": None,
        "messages": None,
        "agents": None,
        "concurrent_calls": None,
        "memory_facts": None,
        "knowledge_chunks": None,
    }
    assert by_id["holding"] == {"memory_facts": 0, "knowledge_chunks": 0}


async def test_the_listing_has_the_default_org_first_and_the_name_defaults_to_the_slug(
    ops_http: httpx.AsyncClient,
) -> None:
    await added(ops_http, "tienda-sur")
    listed = (await ops_http.get(ORGS)).json()
    assert [org["slug"] for org in listed] == ["default", AN_ORG.slug, "tienda-sur"]
    assert listed[-1]["name"] == "tienda-sur"


async def test_a_slug_taken_is_409_and_a_slug_that_is_not_one_is_400(
    ops_http: httpx.AsyncClient,
) -> None:
    await added(ops_http, "tienda-sur")
    assert (await ops_http.post(ORGS, json={"slug": "tienda-sur"})).status_code == 409
    refused = await ops_http.post(ORGS, json={"slug": "Tienda Sur"})
    assert refused.status_code == 400
    assert "lowercase" in refused.json()["detail"]


async def test_an_org_nobody_typed_is_404_on_every_door(ops_http: httpx.AsyncClient) -> None:
    assert (await ops_http.get(f"{ORGS}/nobody")).status_code == 404
    assert (await ops_http.post(f"{ORGS}/nobody/keys", json={})).status_code == 404
    assert (await ops_http.put(f"{ORGS}/nobody/quotas", json={})).status_code == 404
    assert (await ops_http.delete(f"{ORGS}/nobody")).status_code == 404


async def test_quotas_are_replaced_whole_and_a_limit_left_out_is_no_limit(
    ops_http: httpx.AsyncClient,
) -> None:
    set_once = await ops_http.put(
        f"{ORGS}/{AN_ORG.slug}/quotas", json={"minutes": 100, "agents": 3}
    )
    assert set_once.json() == {
        "minutes": 100,
        "messages": None,
        "agents": 3,
        "concurrent_calls": None,
        "memory_facts": None,
        "knowledge_chunks": None,
    }
    set_again = await ops_http.put(
        f"{ORGS}/{AN_ORG.slug}/quotas", json={"messages": 5, "memory_facts": 0}
    )
    assert set_again.json() == {
        "minutes": None,
        "messages": 5,
        "agents": None,
        "concurrent_calls": None,
        "memory_facts": 0,
        "knowledge_chunks": None,
    }
    kept = (await ops_http.get(f"{ORGS}/{AN_ORG.id}")).json()["quotas"]
    assert (kept["messages"], kept["memory_facts"]) == (5, 0), "zero is a limit, not an absence"
    negative = await ops_http.put(f"{ORGS}/{AN_ORG.slug}/quotas", json={"minutes": -1})
    assert negative.status_code == 400


async def test_removing_is_refused_while_a_live_key_or_a_route_names_the_org(
    ops_http: httpx.AsyncClient, routes: MemoryRoutes
) -> None:
    org = await added(ops_http, "tienda-sur")
    issued = (await ops_http.post(f"{ORGS}/{org['id']}/keys", json={"label": "the shop"})).json()
    refused = await ops_http.delete(f"{ORGS}/tienda-sur")
    assert refused.status_code == 409 and "live keys" in refused.json()["detail"]
    hashed = fingerprint(str(issued["key"]))
    assert (await ops_http.post(f"/v1/ops/keys/{hashed}/revoke")).status_code == 200
    await routes.put(
        Route(org=org["id"], agent="tienda-sur", channel="phone", number="+34910000000")
    )
    refused = await ops_http.delete(f"{ORGS}/tienda-sur")
    assert refused.status_code == 409 and "routes" in refused.json()["detail"]
    await routes.remove(org["id"], "+34910000000")
    assert (await ops_http.delete(f"{ORGS}/tienda-sur")).status_code == 204
    assert (await ops_http.get(f"{ORGS}/tienda-sur")).status_code == 404


async def test_a_key_is_issued_under_its_org_and_listed_there_and_nowhere_else(
    ops_http: httpx.AsyncClient,
) -> None:
    org = await added(ops_http, "tienda-sur")
    issued = (await ops_http.post(f"{ORGS}/tienda-sur/keys", json={"label": "the shop"})).json()
    assert issued["org"] == org["id"]
    theirs = (await ops_http.get(f"{ORGS}/{org['id']}/keys")).json()
    assert [row["label"] for row in theirs] == ["the shop"]
    ours = (await ops_http.get(f"{ORGS}/{AN_ORG.slug}/keys")).json()
    assert "the shop" not in [row["label"] for row in ours]
