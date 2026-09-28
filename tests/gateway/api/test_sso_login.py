"""Tests for sign-in at an org's provider: out with a challenge, back with a code, the domains."""

from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest

from pinecall.domain.person import Role
from pinecall.gateway.app import app
from pinecall.tenancy import orgs, people, sso
from pinecall.tenancy.signin import TRIES
from pinecall.tenancy.sso import Client, OrgSso
from tests.conftest import Knocking, postgres
from tests.fakes.idp import IdentityProvider

START = "/v1/login/sso"
CALLBACK = "/v1/login/sso/callback"
ANA = "ana@clinica.test"


@pytest.fixture
async def idp(knocking: Knocking) -> AsyncIterator[IdentityProvider]:
    """The org's provider at https://idp.test, the only thing the gateway's HTTP reaches."""
    provider = IdentityProvider()
    async with httpx.AsyncClient(transport=provider.transport()) as http:
        connections = replace(knocking.gateway.connections, http=http)
        app.state.gateway = replace(knocking.gateway, connections=connections)
        yield provider


async def wired(knocking: Knocking, role: Role | None = None) -> None:
    """The org signs in clinica.test's addresses with its provider."""
    connections = knocking.gateway.connections
    client = Client("https://idp.test", "the-client", "shh-made-up")
    wanted = OrgSso(knocking.org.id, client, ("clinica.test",), role=role)
    await sso.put_sso(connections.pool, connections.vault, wanted)


def browser(knocking: Knocking) -> httpx.AsyncClient:
    """A browser that does not follow a redirect, so the test reads where it goes."""
    return httpx.AsyncClient(base_url=knocking.url, follow_redirects=False)


async def went_out(page: httpx.AsyncClient, **params: str) -> httpx.URL:
    """Where the start sends the browser."""
    answer = await page.get(START, params={"org": "clinica-norte", **params})
    assert answer.status_code == 302, answer.text
    return httpx.URL(answer.headers["location"])


async def came_back(
    page: httpx.AsyncClient, idp: IdentityProvider, out: httpx.URL, email: str = ANA
) -> httpx.URL:
    """Where the callback sends the browser, once the provider vouched for the address."""
    idp.id_token = idp.signed(nonce=out.params["nonce"], email=email)
    answer = await page.get(CALLBACK, params={"state": out.params["state"], "code": "a-code"})
    assert answer.status_code == 302, answer.text
    return httpx.URL(answer.headers["location"])


@postgres
async def test_a_member_goes_out_with_a_challenge_and_comes_back_with_a_login_code(
    knocking: Knocking, idp: IdentityProvider
) -> None:
    await wired(knocking)
    member = await people.invite(
        knocking.gateway.connections.pool,
        knocking.org.id,
        people.Invitee(ANA, "Ana", "developer"),
        seats=None,
    )
    async with browser(knocking) as page:
        out = await went_out(page)
        back = await came_back(page, idp, out)
        signed = await page.post("/v1/login", json={"code": back.params["login"]})
    assert (out.host, out.path) == ("idp.test", "/authorize")
    assert out.params["code_challenge_method"] == "S256"
    assert out.params["redirect_uri"] == "https://box.test/v1/login/sso/callback"
    assert idp.exchanged[0]["code_verifier"], "the verifier went to the token endpoint only"
    assert back.path == "/"
    assert back.params["login"].startswith("lc_")
    assert "pc_" not in str(back), "no key rides a URL"
    assert (signed.json()["subject"], signed.json()["org"]) == (member.member.id, knocking.org.id)


@postgres
async def test_a_sign_in_begun_at_a_terminal_lands_on_its_card(
    knocking: Knocking, idp: IdentityProvider
) -> None:
    await wired(knocking, role="qa")
    async with browser(knocking) as page:
        out = await went_out(page, pairing="cli_waiting")
        back = await came_back(page, idp, out)
    assert (back.path, back.params["c"]) == ("/cli", "cli_waiting")
    assert back.params["login"].startswith("lc_")


@postgres
@pytest.mark.usefixtures("idp")
async def test_an_org_unknown_and_one_with_no_provider_are_the_same_404(knocking: Knocking) -> None:
    await orgs.create(knocking.gateway.connections.pool, "tienda-sur", "Tienda Sur")
    async with browser(knocking) as page:
        unknown = await page.get(START, params={"org": "nobody"})
        unwired = await page.get(START, params={"org": "tienda-sur"})
    assert (unknown.status_code, unwired.status_code) == (404, 404)
    assert "signs in with no identity provider" in unwired.json()["detail"]


@postgres
async def test_a_state_nobody_began_is_the_one_refusal_answered_as_json(
    knocking: Knocking, idp: IdentityProvider
) -> None:
    await wired(knocking, role="qa")
    async with browser(knocking) as page:
        invented = await page.get(CALLBACK, params={"state": "st_nobody", "code": "x"})
        out = await went_out(page)
        await came_back(page, idp, out)
        replayed = await page.get(CALLBACK, params={"state": out.params["state"], "code": "x"})
    assert (invented.status_code, replayed.status_code) == (400, 400)


@postgres
@pytest.mark.usefixtures("idp")
async def test_a_refusal_at_the_provider_goes_back_to_the_sign_in_page_in_words(
    knocking: Knocking,
) -> None:
    await wired(knocking, role="qa")
    async with browser(knocking) as page:
        out = await went_out(page)
        answer = await page.get(
            CALLBACK, params={"state": out.params["state"], "error": "access_denied"}
        )
    back = httpx.URL(answer.headers["location"])
    assert (answer.status_code, back.path) == (302, "/")
    assert back.params["refused"] == "the sign-in was refused at the provider: access_denied"


@postgres
async def test_an_address_of_another_domain_or_nobody_the_org_seats_is_refused(
    knocking: Knocking, idp: IdentityProvider
) -> None:
    await wired(knocking)
    async with browser(knocking) as page:
        foreign = await came_back(page, idp, await went_out(page), email="ana@elsewhere.test")
        stranger = await came_back(page, idp, await went_out(page), email="nobody@clinica.test")
    assert "is not in a domain clinica-norte signs in with" in foreign.params["refused"]
    assert "nobody in clinica-norte answers to" in stranger.params["refused"]
    listed = await people.listed(knocking.gateway.connections.pool, knocking.org.id)
    assert listed == [], "nobody was seated"


@postgres
async def test_a_provider_told_to_seats_somebody_uninvited_with_its_role(
    knocking: Knocking, idp: IdentityProvider
) -> None:
    await wired(knocking, role="supervisor")
    async with browser(knocking) as page:
        await came_back(page, idp, await went_out(page))
    listed = await people.listed(knocking.gateway.connections.pool, knocking.org.id)
    assert [(member.email, member.role, member.status) for member in listed] == [
        (ANA, "supervisor", "active")
    ]


@postgres
async def test_discovery_names_the_orgs_of_a_domain_and_nothing_about_who_exists(
    knocking: Knocking,
) -> None:
    await wired(knocking)
    async with browser(knocking) as page:
        found = await page.post("/v1/login/sso/discover", json={"email": " Nobody@Clinica.test "})
        elsewhere = await page.post("/v1/login/sso/discover", json={"email": "a@elsewhere.test"})
        probe = await page.post("/v1/login/sso/discover", json={"email": ""})
    assert found.json() == {
        "orgs": [{"org": knocking.org.id, "slug": "clinica-norte", "name": "Clinica Norte"}]
    }
    assert elsewhere.json() == {"orgs": []}
    assert (probe.status_code, probe.json()) == (200, {"orgs": []})


@postgres
async def test_discovery_is_throttled_like_the_login(knocking: Knocking) -> None:
    async with browser(knocking) as page:
        answers = [
            (await page.post("/v1/login/sso/discover", json={"email": "a@x.test"})).status_code
            for _ in range(TRIES)
        ]
        stopped = await page.post("/v1/login/sso/discover", json={"email": "a@x.test"})
    assert answers == [200] * TRIES
    assert stopped.status_code == 429


@postgres
async def test_box_wide_google_sign_in_is_not_in_this_version(knocking: Knocking) -> None:
    async with browser(knocking) as page:
        start = await page.get("/v1/login/google")
        back = await page.get("/v1/login/google/callback", params={"state": "x", "code": "y"})
    assert (start.status_code, back.status_code) == (503, 503)
    assert "sign in with a password or the org's provider" in start.json()["detail"]
