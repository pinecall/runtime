"""A stranger makes an org on the cloud: the trial, the admin, the key, the code — and no box."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.signup import FREE_TRIAL, NOT_HERE, TAKEN, TOO_MANY
from pinecall.auth.keys import MemoryKeys
from pinecall.auth.throttle import TRIES_PER_WINDOW
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import KEY_SCOPES
from tests.api.conftest import A_DEV_KEY, A_LIVEKIT, A_VAULT_KEY, AN_OPS_KEY, over_the_asgi_app

pytestmark = pytest.mark.unit

SIGNUP = "/v1/signup"
LOGIN = "/v1/login"
A_PASSWORD = "correct horse battery staple"
TIENDA = {
    "org": "tienda-sur",
    "name": "Tienda Sur",
    "email": "ana@tiendasur.uy",
    "person": "Ana",
    "password": A_PASSWORD,
}


@pytest.fixture
def settings() -> Settings:
    """Pinecall's cloud: the one gateway that takes a sign-up."""
    return Settings(
        dev_key=A_DEV_KEY,
        ops_key=AN_OPS_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        vault_key=A_VAULT_KEY,
        cloud=True,
    )


@pytest.fixture
async def stranger(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """Somebody with no key at all: the person on the landing page."""
    http = over_the_asgi_app("")
    yield http
    await http.aclose()


async def signed_up(stranger: httpx.AsyncClient, **changed: Any) -> httpx.Response:
    return await stranger.post(SIGNUP, json={**TIENDA, **changed})


async def test_a_signup_makes_the_org_on_the_trial_with_its_admin_and_hands_over_the_key(
    stranger: httpx.AsyncClient, orgs: MemoryOrgs, keys: MemoryKeys
) -> None:
    answer = await signed_up(stranger)
    assert answer.status_code == 201, answer.text
    body = answer.json()
    org = await orgs.find("tienda-sur")
    assert org is not None and org.name == "Tienda Sur"
    assert await orgs.quotas_of(org.id) == FREE_TRIAL
    assert (body["org"], body["slug"], body["env"], body["label"]) == (
        org.id,
        "tienda-sur",
        "production",
        "signup",
    )
    assert body["scopes"] == sorted(KEY_SCOPES), "the first person owns the org"
    assert (body["member"]["role"], body["member"]["status"]) == ("admin", "active")
    assert body["subject"] == body["member"]["id"] and body["name"] == "Ana"
    assert await keys.verify(body["key"]) is not None
    assert A_PASSWORD not in answer.text


async def test_the_code_logs_a_browser_in_and_the_password_logs_the_person_in_after(
    stranger: httpx.AsyncClient,
) -> None:
    body = (await signed_up(stranger)).json()
    browser = await stranger.post(LOGIN, json={"code": body["code"], "device": "chrome"})
    assert browser.status_code == 200, browser.text
    assert browser.json()["key"] != body["key"] and browser.json()["org"] == body["org"]
    later = await stranger.post(
        LOGIN, json={"org": "tienda-sur", "email": TIENDA["email"], "password": A_PASSWORD}
    )
    assert later.status_code == 200, later.text


async def test_a_box_of_its_own_takes_no_sign_ups(
    stranger: httpx.AsyncClient, settings: Settings, orgs: MemoryOrgs
) -> None:
    from pinecall.api import _deps
    from pinecall.api.app import app

    a_box = settings.model_copy(update={"cloud": False})
    app.dependency_overrides[_deps.a_settings] = lambda: a_box
    answer = await signed_up(stranger)
    assert (answer.status_code, answer.json()["detail"]) == (403, NOT_HERE)
    assert await orgs.find("tienda-sur") is None


async def test_the_refusals_are_sentences_and_a_refused_signup_makes_no_org(
    stranger: httpx.AsyncClient, orgs: MemoryOrgs
) -> None:
    assert (await signed_up(stranger)).status_code == 201
    taken = await signed_up(stranger, email="otra@tiendasur.uy")
    assert (taken.status_code, taken.json()["detail"]) == (409, TAKEN.format(slug="tienda-sur"))
    bad_slug = await signed_up(stranger, org="Tienda Sur")
    assert bad_slug.status_code == 400 and "lowercase" in bad_slug.json()["detail"]
    short = await signed_up(stranger, org="corta", password="short")
    assert short.status_code == 400 and "characters" in short.json()["detail"]
    no_email = await signed_up(stranger, org="sin-mail", email="ana")
    assert no_email.status_code == 400 and "@" in no_email.json()["detail"]
    assert [org.slug for org in await orgs.listed()][-1] == "tienda-sur", "nothing half-made"


async def test_the_sixth_signup_from_one_place_in_a_minute_is_throttled(
    stranger: httpx.AsyncClient,
) -> None:
    for n in range(TRIES_PER_WINDOW):
        made = await signed_up(stranger, org=f"org-{n}", email=f"p{n}@x.uy")
        assert made.status_code == 201, made.text
    sixth = await signed_up(stranger, org="org-more", email="more@x.uy")
    assert (sixth.status_code, sixth.json()["detail"]) == (429, TOO_MANY)
