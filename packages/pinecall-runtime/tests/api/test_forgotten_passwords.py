"""Before signing in: which orgs a password opens, and a forgotten one handed back by the admin."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.accounts.login import NOBODY_ANYWHERE
from pinecall.api.accounts.members import NO_SUCH_MEMBER, NOT_ACTIVE
from pinecall.auth.keys import NOT_OPENED, KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.throttle import TRIES_PER_WINDOW
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.types import Org
from tests.api.conftest import A_KEY, A_RECORD, AN_ORG, over_the_asgi_app
from tests.api.test_members_and_login import A_PASSWORD, BERNA, LOGIN, accepted, invited

pytestmark = pytest.mark.unit

ORGS = "/v1/login/orgs"
A_NEW_PASSWORD = "a much better horse battery staple"
THE_SHOP = Org(id="org_shop", slug="tienda-sur", name="Tienda Sur")
A_DEV_KEY = "pk_test_a_developer"
A_DEV = KeyRecord(key_id="k_dev", org=A_RECORD.org, scopes=frozenset({"app", "calls"}))


@pytest.fixture
def orgs() -> MemoryOrgs:
    return MemoryOrgs([AN_ORG, THE_SHOP])


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_DEV_KEY: A_DEV})


async def test_a_password_says_which_orgs_it_signs_in_to_and_mints_no_key(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    await accepted(stranger, (await invited(tenant_http))["token"])
    before = len(await keys.listed(A_RECORD.org))
    answered = await stranger.post(ORGS, json={"email": BERNA["email"], "password": A_PASSWORD})
    assert answered.status_code == 200
    assert answered.json() == {
        "orgs": [{"org": AN_ORG.id, "slug": AN_ORG.slug, "name": AN_ORG.name, "role": "developer"}]
    }
    assert len(await keys.listed(A_RECORD.org)) == before


async def test_a_wrong_password_and_a_stranger_are_one_sentence_and_both_are_throttled(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    await accepted(stranger, (await invited(tenant_http))["token"])
    wrong = await stranger.post(ORGS, json={"email": BERNA["email"], "password": "nope nope nope"})
    nobody = await stranger.post(ORGS, json={"email": "who@nowhere.uy", "password": A_PASSWORD})
    assert (wrong.status_code, wrong.json()) == (nobody.status_code, nobody.json())
    assert wrong.json()["detail"] == NOBODY_ANYWHERE
    for _ in range(TRIES_PER_WINDOW):
        await stranger.post(ORGS, json={"email": "who@nowhere.uy", "password": "x"})
    assert (
        await stranger.post(ORGS, json={"email": "who@nowhere.uy", "password": "x"})
    ).status_code == 429


async def test_the_admin_hands_back_a_link_that_sets_a_new_password_and_spends_the_old_one(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    member = (await accepted(stranger, (await invited(tenant_http))["token"]))["member"]
    first = await tenant_http.post(f"/v1/members/{member['id']}/reset")
    second = await tenant_http.post(f"/v1/members/{member['id']}/reset")
    assert (first.status_code, second.status_code) == (201, 201)
    assert (
        second.json()["token"].startswith("inv_") and second.json()["member"]["id"] == member["id"]
    )
    stale = await stranger.post(
        f"/v1/invitations/{first.json()['token']}", json={"password": A_NEW_PASSWORD}
    )
    assert stale.status_code == 404, "the newest link is the only link"
    await accepted(stranger, second.json()["token"], password=A_NEW_PASSWORD)
    old = await stranger.post(LOGIN, json={"email": BERNA["email"], "password": A_PASSWORD})
    new = await stranger.post(LOGIN, json={"email": BERNA["email"], "password": A_NEW_PASSWORD})
    assert (old.status_code, new.status_code) == (401, 200)


async def test_a_member_who_is_not_active_is_not_reset_and_a_disabled_one_stays_disabled(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    pending = await invited(tenant_http)
    refused = await tenant_http.post(f"/v1/members/{pending['member']['id']}/reset")
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        NOT_ACTIVE.format(email=BERNA["email"], status="invited"),
    )
    member = (await accepted(stranger, pending["token"]))["member"]
    link = (await tenant_http.post(f"/v1/members/{member['id']}/reset")).json()["token"]
    await tenant_http.patch(f"/v1/members/{member['id']}", json={"status": "disabled"})
    used = await stranger.post(f"/v1/invitations/{link}", json={"password": A_NEW_PASSWORD})
    assert used.status_code == 404
    listed = (await tenant_http.get("/v1/members")).json()["members"]
    assert [one["status"] for one in listed] == ["disabled"]
    missing = await tenant_http.post("/v1/members/m_nobody/reset")
    assert missing.json()["detail"] == NO_SUCH_MEMBER.format(id="m_nobody")


async def test_resetting_takes_team(wired: None) -> None:  # noqa: ARG001
    async with over_the_asgi_app(f"Bearer {A_DEV_KEY}") as developer:
        refused = await developer.post("/v1/members/m_1/reset")
    assert refused.json()["detail"] == NOT_OPENED.format(scope="team", opens="app · calls")
