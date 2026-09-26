"""A person is their email: one password across the orgs, a seat at once in a second, a switch."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.accounts.login import NOBODY_ANYWHERE
from pinecall.api.accounts.org_switch import NOT_THERE, ONE_ORG_EACH
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.types import ROLE_SCOPES
from tests.api.conftest import A_RECORD, over_the_asgi_app

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"
LOGIN = "/v1/login"
A_PASSWORD = "correct horse battery staple"
JP = {"email": "jp@cloudacio.com", "name": "JP", "role": "admin"}


async def a_second_org(orgs: MemoryOrgs) -> str:
    """One more tenant beside the one the suite's key belongs to."""
    made = await orgs.create("cloudacio", "Cloudacio")
    assert made is not None
    return made.id


async def invited_here(tenant_http: httpx.AsyncClient, **changed: Any) -> dict[str, Any]:
    answer = await tenant_http.post(MEMBERS, json={**JP, **changed})
    assert answer.status_code == 201, answer.text
    return answer.json()


async def invited_there(ops_http: httpx.AsyncClient, org: str, **changed: Any) -> dict[str, Any]:
    answer = await ops_http.post(f"/v1/ops/orgs/{org}/members", json={**JP, **changed})
    assert answer.status_code == 201, answer.text
    return answer.json()


async def accepted(stranger: httpx.AsyncClient, token: str) -> dict[str, Any]:
    answer = await stranger.post(
        f"/v1/invitations/{token}", json={"password": A_PASSWORD, "device": "laptop"}
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_a_person_invited_into_a_second_org_is_seated_at_once_with_no_second_password(
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
) -> None:
    # Invited by the operator, whose link vouches for the address (0048): a link the clinic's
    # admin was handed would prove nothing, and the second org would invite JP like anybody.
    first = await invited_there(ops_http, A_RECORD.org, role="developer")
    await accepted(stranger, first["token"])
    other = await a_second_org(orgs)

    second = await invited_there(ops_http, other)

    # No link, no password screen: the row is active, and the same password opens it.
    assert second["token"] is None and second["expires_at"] is None
    assert second["member"]["status"] == "active"
    signed = await stranger.post(
        LOGIN, json={"org": "cloudacio", "email": JP["email"], "password": A_PASSWORD}
    )
    assert signed.status_code == 200, signed.text
    # An admin's key, the person's own: every door their role opens, and no world of its own.
    assert (signed.json()["org"], signed.json()["scopes"]) == (
        other,
        sorted(ROLE_SCOPES["admin"]),
    )


async def test_a_login_with_no_org_lands_in_the_oldest_org_of_theirs(
    tenant_http: httpx.AsyncClient,
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
) -> None:
    first = await invited_here(tenant_http, role="qa")
    await accepted(stranger, first["token"])
    await invited_there(ops_http, await a_second_org(orgs))

    signed = await stranger.post(LOGIN, json={"email": JP["email"], "password": A_PASSWORD})

    assert signed.status_code == 200, signed.text
    assert signed.json()["org"] == A_RECORD.org
    wrong = await stranger.post(LOGIN, json={"email": JP["email"], "password": "not it"})
    assert (wrong.status_code, wrong.json()["detail"]) == (401, NOBODY_ANYWHERE)


async def test_a_row_invited_before_the_person_existed_is_seated_at_their_first_login_there(
    tenant_http: httpx.AsyncClient,
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
) -> None:
    # Invited into both orgs first, then accepted in one: the other row is still `invited`, with
    # a link the person never needs to open. The box hands its link over; the clinic's admin is
    # handed none for somebody pending elsewhere — that link would choose JP's one password.
    other = await a_second_org(orgs)
    there = await invited_there(ops_http, other)
    assert there["token"] is not None
    here = await invited_here(tenant_http)
    assert here["token"] is None and here["member"]["status"] == "invited"
    await accepted(stranger, there["token"])

    signed = await stranger.post(
        LOGIN, json={"org": A_RECORD.org, "email": JP["email"], "password": A_PASSWORD}
    )

    assert signed.status_code == 200, signed.text
    listed = (await tenant_http.get(MEMBERS)).json()["members"]
    assert [m["status"] for m in listed] == ["active"]


async def test_the_console_lists_the_persons_orgs_and_switches_with_a_key_for_the_same_person(
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
) -> None:
    first = await invited_there(ops_http, A_RECORD.org, role="developer")  # vouched: verified
    signed = await accepted(stranger, first["token"])
    other = await a_second_org(orgs)
    await invited_there(ops_http, other, role="qa")

    async with over_the_asgi_app(f"Bearer {signed['key']}") as jp:
        listed = (await jp.get("/v1/login/orgs")).json()["orgs"]
        moved = await jp.post("/v1/login/org", json={"org": "cloudacio"})
        elsewhere = await jp.post("/v1/login/org", json={"org": "nowhere"})

    assert [(one["slug"], one["role"], one["here"]) for one in listed] == [
        (A_RECORD.org, "developer", True),
        ("cloudacio", "qa", False),
    ]
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert (body["org"], body["env"], body["name"]) == (other, signed["env"], "JP")
    assert body["scopes"] == sorted(ROLE_SCOPES["qa"])
    assert body["subject"] != signed["subject"]
    assert (elsewhere.status_code, elsewhere.json()["detail"]) == (
        403,
        NOT_THERE.format(org="nowhere"),
    )


async def test_a_machine_key_names_nobody_and_has_no_org_to_switch_to(
    tenant_http: httpx.AsyncClient,
) -> None:
    listed = await tenant_http.get("/v1/login/orgs")
    assert (listed.status_code, listed.json()["detail"]) == (403, ONE_ORG_EACH)


async def test_a_password_chosen_at_an_invitation_is_the_persons_password_everywhere(
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    orgs: MemoryOrgs,
) -> None:
    # Seated into the second org on the first password; a re-invite here spends a NEW password
    # at acceptance, and the second org opens with the new one and not the old.
    first = await invited_there(ops_http, A_RECORD.org)  # vouched: verified
    await accepted(stranger, first["token"])
    await invited_there(ops_http, await a_second_org(orgs))
    again = await ops_http.post(f"/v1/ops/orgs/{A_RECORD.org}/members", json=JP)
    assert again.status_code == 409  # accepted here: they log in, nobody re-invites them

    old = await stranger.post(
        LOGIN, json={"org": "cloudacio", "email": JP["email"], "password": A_PASSWORD}
    )
    assert old.status_code == 200, old.text


async def test_an_email_typed_in_capitals_or_with_a_space_is_the_same_person(
    tenant_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
) -> None:
    first = await invited_here(tenant_http, email="JP@Cloudacio.com")
    await accepted(stranger, first["token"])

    signed = await stranger.post(
        LOGIN, json={"email": " jp@CLOUDACIO.com ", "password": A_PASSWORD}
    )

    assert signed.status_code == 200, signed.text
    assert first["member"]["email"] == JP["email"]
