"""A deployed agent is held by a key issued for a machine — never by a person who logged in."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.login import NOT_A_MEMBER
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.types import HOLDING, ROLE_SCOPES
from tests.api.conftest import A_KEY, A_RECORD

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"
LOGIN = "/v1/login"
THE_OTHER_WORLD = "/v1/login/env"
A_PASSWORD = "correct horse battery staple"
CARLA: dict[str, Any] = {
    "email": "carla@clinica.uy",
    "name": "Carla",
    "role": "developer",
    "agents": [],
}

# What a developer's role presets, and the same set without the one scope a person may not carry
# in the deployed world. Spelled from the preset so a role re-cut tomorrow moves both.
DEVELOPER = sorted(ROLE_SCOPES["developer"])
ON_A_LAPTOP_ONLY = sorted(ROLE_SCOPES["developer"] - {HOLDING})

# A key that names a member no table has: what an operator's `keys issue --subject` can make, and
# what a member removed under a live key would leave behind.
A_GHOSTS_KEY = "pk_test_a_key_for_nobody"


@pytest.fixture
def keys() -> MemoryKeys:
    ghost = KeyRecord(key_id="k_ghost", org=A_RECORD.org, subject="m_nobody", name="Nobody")
    return MemoryKeys({A_KEY: A_RECORD, A_GHOSTS_KEY: ghost})


async def a_member(tenant: httpx.AsyncClient, stranger: httpx.AsyncClient, **changed: Any) -> str:
    """Carla invited and accepting, so there is somebody with a password to log in with."""
    invited = await tenant.post(MEMBERS, json={**CARLA, **changed})
    assert invited.status_code == 201, invited.text
    token = str(invited.json()["token"])
    accepted = await stranger.post(f"/v1/invitations/{token}", json={"password": A_PASSWORD})
    assert accepted.status_code == 200, accepted.text
    return str(invited.json()["member"]["id"])


async def logged_in(stranger: httpx.AsyncClient, env: str) -> dict[str, Any]:
    said = {"org": A_RECORD.org, "email": CARLA["email"], "password": A_PASSWORD, "env": env}
    answer = await stranger.post(LOGIN, json=said)
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def test_a_developer_holds_agents_on_their_laptop_and_none_in_production(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """The same person, the same role, the two worlds: `app` is development's and only there."""
    await a_member(tenant_http, stranger)
    assert (await logged_in(stranger, "production"))["scopes"] == ON_A_LAPTOP_ONLY
    assert (await logged_in(stranger, "sandbox"))["scopes"] == DEVELOPER


async def test_the_key_the_invitation_hands_over_follows_the_same_rule(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """It is the first key a person ever holds, and it is minted by the same rule as the rest."""
    invited = await tenant_http.post(MEMBERS, json=CARLA)
    token = str(invited.json()["token"])
    answer = await stranger.post(f"/v1/invitations/{token}", json={"password": A_PASSWORD})
    assert answer.json()["scopes"] == ON_A_LAPTOP_ONLY


async def test_looking_the_other_way_reads_the_role_and_not_the_key_that_asked(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """A production key holds no `app`; the development key it asks for must, or a dev is stuck."""
    await a_member(tenant_http, stranger)
    key = str((await logged_in(stranger, "production"))["key"])
    answer = await stranger.post(
        THE_OTHER_WORLD, json={"env": "sandbox"}, headers={"Authorization": f"Bearer {key}"}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["scopes"] == DEVELOPER


async def test_a_disabled_person_does_not_mint_themselves_a_second_world(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    """Disabling revokes their keys; a key that outlived one — an operator's — mints nothing."""
    member = await a_member(tenant_http, stranger)
    key = str((await logged_in(stranger, "production"))["key"])
    disabled = await tenant_http.patch(f"{MEMBERS}/{member}", json={"status": "disabled"})
    assert disabled.status_code == 200, disabled.text
    answer = await stranger.post(
        THE_OTHER_WORLD, json={"env": "sandbox"}, headers={"Authorization": f"Bearer {key}"}
    )
    assert answer.status_code == 401, "their keys were revoked with them"


async def test_a_key_whose_person_the_table_no_longer_has_mints_no_second_world(
    stranger: httpx.AsyncClient,
) -> None:
    """An operator may issue a key naming anybody; the world-changing door checks the row."""
    answer = await stranger.post(
        THE_OTHER_WORLD,
        json={"env": "sandbox"},
        headers={"Authorization": f"Bearer {A_GHOSTS_KEY}"},
    )
    assert (answer.status_code, answer.json()["detail"]) == (403, NOT_A_MEMBER)
