"""Continue with Google, box-wide: wired by the operator, entered by any org's member by address."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.box_signin import EMPTY
from pinecall.api.box_signin import NOT_WIRED as NOTHING_TO_FORGET
from pinecall.api.login import A_BROWSER
from pinecall.api.login_google import (
    DISABLED_EVERYWHERE,
    NOBODY_HERE,
    NOT_WIRED,
    THE_BOX,
    THEIR_OWN_PROVIDER,
)
from pinecall.api.login_sso import NO_HANDSHAKE, THE_CARD, THE_CONSOLE
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.openid import SCOPE
from pinecall.auth.sso import Handshakes
from pinecall.orgs.box import BoxSettings, MemoryBoxSettings
from pinecall.orgs.signin import PROVIDERS
from pinecall.orgs.sso import Sso
from pinecall.orgs.table import MemoryOrgs
from pinecall.orgs.vault import NO_VAULT_KEY
from pinecall.types import Member, OrgSso
from tests.api.conftest import AN_ORG
from tests.api.fake_idp import FakeIdp

pytestmark = pytest.mark.unit

GOOGLE = PROVIDERS["google"].issuer
SIGN_IN = "/v1/login/google"
CALLBACK = "/v1/login/google/callback"
THE_DOOR = "/v1/ops/signin"
A_CLIENT = {"client_id": "12345.apps.googleusercontent.com", "client_secret": "GOCSPX-nobody"}
NICO = Member(
    id="m_nico",
    org=AN_ORG.id,
    email="nico@tiendasur.uy",
    name="Nico",
    role="developer",
    status="active",
)


@pytest.fixture(autouse=True)
def signing_in(sso: Sso | None, http: httpx.AsyncClient, handshakes: Handshakes) -> None:  # noqa: ARG001
    """Every test here signs in: what reaches the provider, the sign-ins, the org tables."""


@pytest.fixture
def idp() -> FakeIdp:
    """Google, scripted: the same three doors, at Google's own issuer, for the operator's client."""
    return FakeIdp(
        issuer=GOOGLE, client_id=A_CLIENT["client_id"], client_secret=A_CLIENT["client_secret"]
    )


@pytest.fixture
def members() -> MemoryMembers:
    """One person of the clinic, active and with no password."""
    return MemoryMembers([NICO])


async def wired(ops_http: httpx.AsyncClient) -> dict[str, Any]:
    answer = await ops_http.put(f"{THE_DOOR}/google", json=A_CLIENT)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def sent_to_google(
    stranger: httpx.AsyncClient, idp: FakeIdp, asking: str = "", **claims: Any
) -> httpx.QueryParams:
    out = await stranger.get(f"{SIGN_IN}{asking}")
    assert out.status_code == 302, out.text
    asked = httpx.URL(out.headers["location"]).params
    idp.challenge = str(asked["code_challenge"])
    idp.vouches_for(str(asked["nonce"]), **claims)
    return asked


async def came_back(stranger: httpx.AsyncClient, asked: httpx.QueryParams) -> httpx.URL:
    back = await stranger.get(f"{CALLBACK}?code=one-authorization-code&state={asked['state']}")
    assert back.status_code == 302, back.text
    return httpx.URL(back.headers["location"])


async def test_the_operator_wires_google_reads_it_back_without_the_secret_and_forgets_it(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    nothing = await ops_http.get(THE_DOOR)
    assert nothing.json() == {
        "google": {
            "configured": False,
            "client_id": None,
            "redirect_uri": f"http://gateway.test{CALLBACK}",
        }
    }
    assert (await stranger.get("/.well-known/pinecall")).json()["google"] is False
    gone = await ops_http.delete(f"{THE_DOOR}/google")
    assert (gone.status_code, gone.json()["detail"]) == (
        404,
        NOTHING_TO_FORGET.format(provider="google"),
    )

    said = await wired(ops_http)

    assert said == {
        "configured": True,
        "client_id": A_CLIENT["client_id"],
        "redirect_uri": f"http://gateway.test{CALLBACK}",
    }
    assert A_CLIENT["client_secret"] not in (await ops_http.get(THE_DOOR)).text
    assert (await stranger.get("/.well-known/pinecall")).json()["google"] is True
    assert (await ops_http.delete(f"{THE_DOOR}/google")).status_code == 204
    assert (await stranger.get("/.well-known/pinecall")).json()["google"] is False
    assert (await stranger.get(SIGN_IN)).status_code == 404
    assert (await stranger.get(SIGN_IN)).json()["detail"] == NOT_WIRED


async def test_wiring_is_refused_when_google_does_not_answer_or_the_body_is_empty(
    ops_http: httpx.AsyncClient, idp: FakeIdp
) -> None:
    empty = await ops_http.put(f"{THE_DOOR}/google", json={**A_CLIENT, "client_secret": ""})
    assert (empty.status_code, empty.json()["detail"]) == (400, EMPTY)
    idp.issuer = "https://idp.test"  # Google's discovery now names somebody else
    down = await ops_http.put(f"{THE_DOOR}/google", json=A_CLIENT)
    assert down.status_code == 400 and "nothing was kept" in down.json()["detail"]
    assert (await ops_http.get(THE_DOOR)).json()["google"]["configured"] is False


class TestWithNoVaultKey:
    @pytest.fixture
    def box_settings(self) -> BoxSettings:
        return MemoryBoxSettings(None)

    async def test_the_secret_cannot_be_kept_and_the_door_says_so(
        self, ops_http: httpx.AsyncClient
    ) -> None:
        refused = await ops_http.put(f"{THE_DOOR}/google", json=A_CLIENT)
        assert (refused.status_code, refused.json()["detail"]) == (503, NO_VAULT_KEY)


async def test_the_redirect_goes_to_google_with_the_operators_client_and_pkce(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, idp: FakeIdp
) -> None:
    await wired(ops_http)
    asked = await sent_to_google(stranger, idp)
    assert asked["client_id"] == A_CLIENT["client_id"] and asked["scope"] == SCOPE
    assert asked["redirect_uri"] == f"http://gateway.test{CALLBACK}"
    assert asked["code_challenge_method"] == "S256" and asked["state"].startswith("st_")


async def test_a_member_google_vouches_for_lands_in_the_oldest_org_of_theirs_with_a_way_in(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, idp: FakeIdp, orgs: MemoryOrgs
) -> None:
    await wired(ops_http)
    asked = await sent_to_google(stranger, idp, email="Nico@TiendaSur.uy")
    landed = await came_back(stranger, asked)
    assert landed.path == THE_CONSOLE and str(landed.params["login"]).startswith("lc_")
    minted = await stranger.post("/v1/login", json={"code": str(landed.params["login"])})
    said = minted.json()
    assert (said["org"], said["subject"], said["env"], said["label"]) == (
        AN_ORG.id,
        NICO.id,
        "sandbox",
        A_BROWSER,
    )
    assert idp.secrets_seen == [A_CLIENT["client_secret"]]
    assert await orgs.find(AN_ORG.id) is not None


async def test_somebody_still_invited_is_seated_by_the_verified_address_in_every_org_of_theirs(
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    idp: FakeIdp,
    members: MemoryMembers,
    orgs: MemoryOrgs,
) -> None:
    other = await orgs.create("tienda-sur", "Tienda Sur")
    assert other is not None
    first = await members.invite(AN_ORG.id, "ana@tiendasur.uy", "Ana", "qa", [])
    second = await members.invite(other.id, "ana@tiendasur.uy", "Ana", "admin", [])
    assert first is not None and second is not None
    await wired(ops_http)

    landed = await came_back(
        stranger, await sent_to_google(stranger, idp, email="ana@tiendasur.uy")
    )

    minted = (await stranger.post("/v1/login", json={"code": str(landed.params["login"])})).json()
    assert minted["subject"] == first.member.id, "the oldest org of theirs"
    assert [row.status for row in await members.orgs_of("ana@tiendasur.uy")] == ["active", "active"]


@pytest.mark.parametrize(
    ("email", "sentence"),
    [
        ("stranger@example.com", NOBODY_HERE.format(email="stranger@example.com")),
        ("nico@tiendasur.uy", DISABLED_EVERYWHERE.format(email="nico@tiendasur.uy")),
    ],
)
async def test_nobody_and_somebody_disabled_everywhere_are_sent_back_with_why(
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    idp: FakeIdp,
    members: MemoryMembers,
    email: str,
    sentence: str,
) -> None:
    await members.update(NICO.org, NICO.id, status="disabled")
    await wired(ops_http)
    landed = await came_back(stranger, await sent_to_google(stranger, idp, email=email))
    assert landed.path == THE_CONSOLE and "login" not in landed.params
    assert str(landed.params["refused"]) == sentence


async def test_an_org_on_its_own_required_provider_is_not_entered_this_way(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, idp: FakeIdp, sso: Sso
) -> None:
    await sso.put(
        OrgSso(
            org=AN_ORG.id,
            issuer="https://idp.test",
            client_id="c",
            client_secret="s",
            domains=("tiendasur.uy",),
            role=None,
            required=True,
        )
    )
    await wired(ops_http)
    landed = await came_back(stranger, await sent_to_google(stranger, idp))
    assert str(landed.params["refused"]) == THEIR_OWN_PROVIDER.format(
        email=NICO.email, org=AN_ORG.id
    )


async def test_an_unverified_address_and_a_refusal_at_google_are_sent_back_with_why(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, idp: FakeIdp
) -> None:
    await wired(ops_http)
    unverified = await came_back(
        stranger, await sent_to_google(stranger, idp, email_verified=False)
    )
    assert "has not verified" in str(unverified.params["refused"])
    asked = await sent_to_google(stranger, idp)
    said_no = await stranger.get(f"{CALLBACK}?error=access_denied&state={asked['state']}")
    assert said_no.status_code == 302
    assert "access_denied" in str(httpx.URL(said_no.headers["location"]).params["refused"])


async def test_a_state_is_spent_once_and_an_orgs_own_state_opens_nothing_here(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, idp: FakeIdp, handshakes: Handshakes
) -> None:
    await wired(ops_http)
    asked = await sent_to_google(stranger, idp)
    await came_back(stranger, asked)
    again = await stranger.get(f"{CALLBACK}?code=c&state={asked['state']}")
    assert again.status_code == 400 and again.json()["detail"] == NO_HANDSHAKE
    theirs = handshakes.open(AN_ORG.id, "http://gateway.test/v1/login/sso/callback")
    crossed = await stranger.get(f"{CALLBACK}?code=c&state={theirs.state}")
    assert crossed.status_code == 400
    ours = handshakes.open(THE_BOX, "http://gateway.test" + CALLBACK, provider="google")
    crossed_back = await stranger.get(f"/v1/login/sso/callback?code=c&state={ours.state}")
    assert crossed_back.status_code == 404


async def test_a_terminal_waiting_to_be_signed_in_gets_its_card_back(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, idp: FakeIdp
) -> None:
    await wired(ops_http)
    pairing = (await stranger.post("/v1/login/pairings", json={"device": "laptop"})).json()["code"]
    landed = await came_back(stranger, await sent_to_google(stranger, idp, f"?pairing={pairing}"))
    assert landed.path == THE_CARD and landed.params["c"] == pairing and "login" in landed.params


async def test_the_sixth_sign_in_from_one_place_in_a_minute_waits(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    await wired(ops_http)
    answers = [(await stranger.get(SIGN_IN)).status_code for _ in range(6)]
    assert answers == [302] * 5 + [429]
