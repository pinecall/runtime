"""The org's people over the real app: invited, accepted, logged in, throttled, disabled, coded."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.accounts.login import (
    NO_CODE,
    NOBODY,
    NOT_A_MEMBER,
    NOT_A_PERSONS_CODE,
    ONE_OR_THE_OTHER,
)
from pinecall.api.accounts.members import ALREADY_A_MEMBER, NO_INVITATION
from pinecall.api.accounts.membership import NOT_BY_HAND
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.throttle import TRIES_PER_WINDOW
from pinecall.settings import Settings
from pinecall.types import ROLE_SCOPES
from tests.api.conftest import A_KEY, A_RECORD, AN_ORG, over_the_asgi_app

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"
LOGIN = "/v1/login"
CODES = "/v1/login/codes"
A_PASSWORD = "correct horse battery staple"
BERNA = {
    "email": "berna@clinica.uy",
    "name": "Berna",
    "role": "developer",
    "agents": ["clinica-norte"],
}


async def invited(tenant_http: httpx.AsyncClient, **changed: Any) -> dict[str, Any]:
    answer = await tenant_http.post(MEMBERS, json={**BERNA, **changed})
    assert answer.status_code == 201, answer.text
    return answer.json()


async def accepted(stranger: httpx.AsyncClient, token: str, **said: Any) -> dict[str, Any]:
    answer = await stranger.post(
        f"/v1/invitations/{token}", json={"password": A_PASSWORD, "device": "laptop", **said}
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_an_invite_answers_the_row_and_the_token_once_and_the_listing_never_shows_it(
    tenant_http: httpx.AsyncClient,
) -> None:
    said = await invited(tenant_http)
    assert said["token"].startswith("inv_")
    assert said["member"]["status"] == "invited"
    assert said["member"]["scopes"] == sorted(ROLE_SCOPES["developer"])
    listing = await tenant_http.get(MEMBERS)
    assert [m["email"] for m in listing.json()["members"]] == ["berna@clinica.uy"]
    assert said["token"] not in listing.text


async def test_accepting_makes_the_member_active_and_hands_them_their_first_key(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    said = await invited(tenant_http)
    signed = await accepted(stranger, said["token"])
    assert signed["member"]["status"] == "active"
    assert (signed["org"], signed["env"], signed["label"]) == (
        A_RECORD.org,
        "sandbox",
        "laptop",
    )
    assert (signed["subject"], signed["name"]) == (said["member"]["id"], "Berna")
    assert signed["scopes"] == sorted(ROLE_SCOPES["developer"])
    record = await keys.verify(signed["key"])
    assert record is not None and record.subject == said["member"]["id"]
    assert record.scopes == ROLE_SCOPES["developer"]
    # The new key opens the tenant's doors as the person: whoami says who, and in which world —
    # the instance's, which a person's key is read in as an identity whatever it was minted with.
    async with over_the_asgi_app(f"Bearer {signed['key']}") as berna:
        who = (await berna.get("/v1/whoami")).json()
    assert (who["name"], who["env"]) == ("Berna", "production")


async def test_a_spent_expired_or_invented_token_is_one_404_and_a_short_password_is_400(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    said = await invited(tenant_http)
    await accepted(stranger, said["token"])
    again = await stranger.post(f"/v1/invitations/{said['token']}", json={"password": A_PASSWORD})
    assert (again.status_code, again.json()["detail"]) == (404, NO_INVITATION)
    # The floor is the box's (`PINECALL_MIN_PASSWORD`), so the test asks the suite's settings for
    # it rather than naming a number the operator is free to move.
    floor = Settings(world="production").min_password
    short = await stranger.post("/v1/invitations/inv_x", json={"password": "a" * (floor - 1)})
    assert short.status_code == 400 and f"at least {floor}" in short.json()["detail"]


async def test_login_with_the_password_mints_a_key_for_that_person_and_device(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    said = await invited(tenant_http, role="supervisor")
    await accepted(stranger, said["token"])
    signed = await stranger.post(
        LOGIN,
        json={
            "org": A_RECORD.org,
            "email": "berna@clinica.uy",
            "password": A_PASSWORD,
            "device": "phone",
        },
    )
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["scopes"] == sorted(ROLE_SCOPES["supervisor"])
    # The person's own key: no world of its own, so the column holds the sandbox
    # (auth/person_keys.py).
    assert (body["label"], body["env"], body["subject"]) == (
        "phone",
        "sandbox",
        said["member"]["id"],
    )


async def test_a_wrong_password_a_wrong_email_and_a_wrong_org_are_one_sentence(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    said = await invited(tenant_http)
    await accepted(stranger, said["token"])
    for body in (
        {"org": A_RECORD.org, "email": "berna@clinica.uy", "password": "wrong password here"},
        {"org": A_RECORD.org, "email": "nobody@clinica.uy", "password": A_PASSWORD},
        {"org": "tienda", "email": "berna@clinica.uy", "password": A_PASSWORD},
    ):
        refused = await stranger.post(LOGIN, json=body)
        assert refused.status_code == 401
        assert refused.json()["detail"] == NOBODY.format(org=body["org"])


async def test_a_member_still_invited_cannot_log_in_and_is_told_nothing_a_stranger_is_not(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    await invited(tenant_http)
    refused = await stranger.post(
        LOGIN, json={"org": A_RECORD.org, "email": "berna@clinica.uy", "password": A_PASSWORD}
    )
    assert (refused.status_code, refused.json()["detail"]) == (401, NOBODY.format(org=A_RECORD.org))


async def test_the_sixth_try_in_a_minute_is_429_whatever_the_password(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    said = await invited(tenant_http)
    await accepted(stranger, said["token"])
    body = {"org": A_RECORD.org, "email": "berna@clinica.uy", "password": "wrong password here"}
    for _ in range(TRIES_PER_WINDOW):
        assert (await stranger.post(LOGIN, json=body)).status_code == 401
    right = await stranger.post(LOGIN, json={**body, "password": A_PASSWORD})
    assert right.status_code == 429
    assert "berna@clinica.uy" in right.json()["detail"]


async def test_disabling_a_member_revokes_their_keys_and_refuses_their_login(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    said = await invited(tenant_http)
    signed = await accepted(stranger, said["token"])
    assert await keys.verify(signed["key"]) is not None
    changed = await tenant_http.patch(
        f"{MEMBERS}/{said['member']['id']}", json={"status": "disabled"}
    )
    assert changed.status_code == 200 and changed.json()["status"] == "disabled"
    assert await keys.verify(signed["key"]) is None, "the person's key is stopped"
    assert await keys.verify(A_KEY) is not None, "the org's own key is not theirs"
    refused = await stranger.post(
        LOGIN, json={"org": A_RECORD.org, "email": "berna@clinica.uy", "password": A_PASSWORD}
    )
    assert refused.status_code == 403 and "disabled" in refused.json()["detail"]
    back = await tenant_http.patch(f"{MEMBERS}/{said['member']['id']}", json={"status": "active"})
    assert back.status_code == 200 and back.json()["status"] == "active"


async def test_a_role_change_presets_the_next_key_and_an_invited_member_is_not_activated_by_hand(
    tenant_http: httpx.AsyncClient,
) -> None:
    said = await invited(tenant_http)
    changed = await tenant_http.patch(
        f"{MEMBERS}/{said['member']['id']}", json={"role": "qa", "agents": ["tienda-sur"]}
    )
    assert changed.status_code == 200
    assert (changed.json()["role"], changed.json()["agents"]) == ("qa", ["tienda-sur"])
    assert changed.json()["scopes"] == sorted(ROLE_SCOPES["qa"])
    by_hand = await tenant_http.patch(
        f"{MEMBERS}/{said['member']['id']}", json={"status": "active"}
    )
    assert by_hand.status_code == 400
    assert by_hand.json()["detail"] == NOT_BY_HAND.format(email="berna@clinica.uy")
    nobody = await tenant_http.patch(f"{MEMBERS}/m_nobody", json={"role": "qa"})
    assert nobody.status_code == 404


async def test_the_refusals_of_the_invite_door_are_sentences(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    said = await invited(tenant_http)
    await accepted(stranger, said["token"])
    again = await tenant_http.post(MEMBERS, json=BERNA)
    assert (again.status_code, again.json()["detail"]) == (
        409,
        ALREADY_A_MEMBER.format(email="berna@clinica.uy"),
    )
    bad_role = await tenant_http.post(
        MEMBERS, json={**BERNA, "email": "ana@clinica.uy", "role": "root"}
    )
    assert bad_role.status_code == 400 and "a role is one of" in bad_role.json()["detail"]
    bad_email = await tenant_http.post(MEMBERS, json={**BERNA, "email": "ana"})
    assert bad_email.status_code == 400 and "one @" in bad_email.json()["detail"]
    async with over_the_asgi_app("") as nobody:
        assert (await nobody.get(MEMBERS)).status_code == 401


async def test_a_code_minted_by_a_key_holder_logs_a_browser_in_with_a_key_of_its_own(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    """`pinecall start` prints ?login=<code>; the browser spends it and holds a key of its own."""
    minted = await tenant_http.post(CODES)
    assert minted.status_code == 200 and minted.json()["code"].startswith("lc_")
    signed = await stranger.post(LOGIN, json={"code": minted.json()["code"]})
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["key"] != A_KEY
    assert (body["org"], body["env"], body["label"]) == (A_RECORD.org, A_RECORD.env, "console")
    assert body["scopes"] == sorted(A_RECORD.scopes)
    assert await keys.verify(body["key"]) is not None
    spent = await stranger.post(LOGIN, json={"code": minted.json()["code"]})
    assert (spent.status_code, spent.json()["detail"]) == (404, NO_CODE)


async def test_a_login_says_one_thing_or_the_other(stranger: httpx.AsyncClient) -> None:
    both = await stranger.post(LOGIN, json={"code": "lc_x", "email": "a@b.c"})
    neither = await stranger.post(LOGIN, json={"org": A_RECORD.org})
    assert (both.status_code, both.json()["detail"]) == (400, ONE_OR_THE_OTHER)
    assert (neither.status_code, neither.json()["detail"]) == (400, ONE_OR_THE_OTHER)
    no_code_door = await stranger.post(CODES)
    assert no_code_door.status_code == 401, "minting a code takes a key"


# ── production redeems the code a person carried to the sandbox ─────────────────

REDEEM = "/v1/login/redeem"


async def a_persons_code(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, **changed: Any
) -> tuple[dict[str, Any], str]:
    """Berna invited, accepted, and a code minted with her own key: what the console carries."""
    said = await invited(tenant_http, **changed)
    signed = await accepted(stranger, said["token"])
    async with over_the_asgi_app(f"Bearer {signed['key']}") as berna:
        minted = await berna.post(CODES)
    assert minted.status_code == 200, minted.text
    return said["member"], minted.json()["code"]


async def test_a_redeemed_code_answers_the_org_and_the_member_as_their_row_says_now_and_once(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """The role is read off the row at the redemption, never off the key the code remembers."""
    member, code = await a_persons_code(tenant_http, stranger)
    await tenant_http.patch(f"{MEMBERS}/{member['id']}", json={"role": "qa"})
    redeemed = await stranger.post(REDEEM, json={"code": code})
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json() == {
        "org": {"id": A_RECORD.org, "slug": AN_ORG.slug, "name": AN_ORG.name},
        "member": {
            "id": member["id"],
            "email": "berna@clinica.uy",
            "name": "Berna",
            "role": "qa",
            "agents": ["clinica-norte"],
            "status": "active",
        },
    }
    again = await stranger.post(REDEEM, json={"code": code})
    assert (again.status_code, again.json()["detail"]) == (404, NO_CODE)


async def test_a_code_a_server_token_minted_redeems_nobody(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """Any key may mint a code; only a member's names somebody the sandbox could seat."""
    minted = await tenant_http.post(CODES)
    refused = await stranger.post(REDEEM, json={"code": minted.json()["code"]})
    assert (refused.status_code, refused.json()["detail"]) == (403, NOT_A_PERSONS_CODE)


async def test_a_member_disabled_since_the_code_was_minted_is_answered_disabled(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """A disabled person is exactly what the sandbox must learn, so production says it."""
    member, code = await a_persons_code(tenant_http, stranger)
    await tenant_http.patch(f"{MEMBERS}/{member['id']}", json={"status": "disabled"})
    redeemed = await stranger.post(REDEEM, json={"code": code})
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["member"]["status"] == "disabled"


async def test_a_member_removed_since_the_code_was_minted_is_nobody(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    member, code = await a_persons_code(tenant_http, stranger)
    assert (await tenant_http.delete(f"{MEMBERS}/{member['id']}")).status_code == 204
    refused = await stranger.post(REDEEM, json={"code": code})
    assert (refused.status_code, refused.json()["detail"]) == (403, NOT_A_MEMBER)


async def test_redeeming_is_not_throttled_a_code_cannot_be_guessed(
    stranger: httpx.AsyncClient,
) -> None:
    """Every sign-in to the sandbox knocks from the sandbox gateway: a per-client count of five
    would be five sign-ins a minute for everybody."""
    for _ in range(TRIES_PER_WINDOW + 1):
        assert (await stranger.post(REDEEM, json={"code": "lc_x"})).status_code == 404
