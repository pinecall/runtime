"""The sign-in at an org's identity provider, end to end, against a provider that signs for real."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from pinecall.accounts import A_BROWSER, WITH_THE_PROVIDER
from pinecall.api.accounts.sso_login import NO_HANDSHAKE, NO_SSO_HERE, THE_CARD, THE_CONSOLE
from pinecall.auth import passwords
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.openid import SCOPE
from pinecall.auth.sso_state import Handshakes
from pinecall.orgs.org_sso import Sso
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.types import Member, OrgSso, Quotas, Role
from tests.api.conftest import AN_ORG
from tests.api.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeIdp

pytestmark = pytest.mark.unit

SIGN_IN = "/v1/login/sso"
CALLBACK = "/v1/login/sso/callback"
A_DOMAIN = "tiendasur.uy"
A_PASSWORD = "a-long-enough-password"
NICO = Member(
    id="m_nico",
    org=AN_ORG.id,
    email=f"nico@{A_DOMAIN}",
    name="Nico",
    role="developer",
    status="active",
)


@pytest.fixture(autouse=True)
def signing_in(sso: Sso | None, http: httpx.AsyncClient, handshakes: Handshakes) -> None:
    """Every test here signs in: the org's wiring, what reaches the provider, the sign-ins."""


@pytest.fixture
def members() -> MemoryMembers:
    """One person of the clinic, active and with no password: the provider is how they sign in."""
    return MemoryMembers([NICO])


async def wired(sso: Sso, *, role: Role | None = None, required: bool = False) -> None:
    """The org wired to the fake provider, as an admin's PUT would have left it."""
    await sso.put(
        OrgSso(
            org=AN_ORG.id,
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            domains=(A_DOMAIN,),
            role=role,
            required=required,
        )
    )


async def test_the_door_says_one_404_for_an_org_wired_to_nothing_and_for_an_org_that_is_not(
    stranger: httpx.AsyncClient,
) -> None:
    """No key at this door, so two sentences would let a stranger walk the box's org slugs."""
    unwired = await stranger.get(f"{SIGN_IN}?org={AN_ORG.slug}")
    nobodys = await stranger.get(f"{SIGN_IN}?org=nobodys")
    assert unwired.status_code == nobodys.status_code == 404
    assert unwired.json()["detail"] == NO_SSO_HERE.format(org=AN_ORG.slug)
    assert nobodys.json()["detail"] == NO_SSO_HERE.format(org="nobodys")


async def sent_to_the_provider(
    stranger: httpx.AsyncClient, idp: FakeIdp, asking: str = f"?org={AN_ORG.slug}", **claims: Any
) -> httpx.QueryParams:
    """The first half: the 302 out, with the provider primed to vouch for whoever comes back."""
    out = await stranger.get(f"{SIGN_IN}{asking}")
    assert out.status_code == 302
    asked = httpx.URL(out.headers["location"]).params
    idp.challenge = str(asked["code_challenge"])
    idp.vouches_for(str(asked["nonce"]), **claims)
    return asked


async def came_back(
    stranger: httpx.AsyncClient, asked: httpx.QueryParams, code: str = "one-authorization-code"
) -> httpx.Response:
    """The second half: the provider sends the person back with a code and this sign-in's state."""
    return await stranger.get(f"{CALLBACK}?code={code}&state={asked['state']}")


async def test_the_redirect_carries_the_client_the_scope_and_a_pkce_challenge(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """What the browser is sent with: everything the provider needs, and nothing of this box's."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    assert asked["response_type"] == "code" and asked["client_id"] == CLIENT_ID
    assert asked["redirect_uri"] == f"http://gateway.test{CALLBACK}"
    assert asked["scope"] == SCOPE and asked["code_challenge_method"] == "S256"
    assert asked["state"].startswith("st_") and asked["nonce"]
    # The verifier itself never travels: what is in the URL is its sha256 and nothing else.
    assert "code_verifier" not in asked


async def test_a_person_the_provider_vouches_for_lands_on_a_one_use_way_in(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """The whole path: the code exchanged, the id_token checked, and a console key waiting."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    back = await came_back(stranger, asked)
    assert back.status_code == 302
    landed = httpx.URL(back.headers["location"])
    assert landed.path == THE_CONSOLE and str(landed.params["login"]).startswith("lc_")
    # No key is ever in that URL: the word is, and the browser spends it for one of its own.
    assert "pk_" not in str(landed)
    minted = await stranger.post("/v1/login", json={"code": str(landed.params["login"])})
    assert minted.status_code == 200
    said = minted.json()
    assert said["subject"] == NICO.id and said["org"] == AN_ORG.id
    assert said["env"] == "sandbox" and said["label"] == A_BROWSER
    # The person's own key: every door their role opens, and the world the request names.
    assert said["scopes"] == sorted(NICO.scopes)
    # And the secret the exchange carried is the one the vault sealed and opened again.
    assert idp.secrets_seen == [CLIENT_SECRET]


async def test_the_state_is_spent_on_first_use(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """A callback replayed — a browser's back button, somebody's log — finishes nothing twice."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    assert (await came_back(stranger, asked)).status_code == 302
    again = await came_back(stranger, asked)
    assert again.status_code == 400 and again.json()["detail"] == NO_HANDSHAKE


async def test_a_state_nobody_minted_finishes_nothing(stranger: httpx.AsyncClient) -> None:
    answer = await stranger.get(f"{CALLBACK}?code=c&state=st_whatever")
    assert answer.status_code == 400 and answer.json()["detail"] == NO_HANDSHAKE


async def test_an_id_token_for_another_sign_in_is_refused(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """The nonce is what says this token answers THIS sign-in, and nothing else says it."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    idp.vouches_for("a-nonce-from-another-call")
    answer = await came_back(stranger, asked)
    assert answer.status_code == 401 and "nonce" in answer.json()["detail"]


async def test_an_id_token_for_another_audience_is_refused(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """A token minted for another client of the same provider is not this gateway's to seat on."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    idp.said["aud"] = "somebody-elses-client"
    answer = await came_back(stranger, asked)
    assert answer.status_code == 401 and "Audience" in answer.json()["detail"]


async def test_an_expired_id_token_is_refused(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    idp.said["exp"] = int(time.time()) - 10
    answer = await came_back(stranger, asked)
    assert answer.status_code == 401 and "expired" in answer.json()["detail"].lower()


async def test_an_id_token_signed_by_nobodys_key_is_refused(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """The signature is checked against the JWKS: a token the provider did not sign is nobody."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    forged = idp.id_token().rsplit(".", 1)[0] + ".not-the-providers-signature"
    idp.id_token = lambda: forged  # type: ignore[method-assign]
    answer = await came_back(stranger, asked)
    assert answer.status_code == 401


async def test_an_unverified_address_seats_nobody(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """An address a directory holds but nobody proved is how a stranger becomes a colleague."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp, email_verified=False)
    answer = await came_back(stranger, asked)
    assert answer.status_code == 401 and "not verified" in answer.json()["detail"]


async def test_an_address_outside_the_orgs_domains_is_refused(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """A provider may vouch for a guest from anywhere; the org says which domains it signs in."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp, email="somebody@elsewhere.test")
    answer = await came_back(stranger, asked)
    assert answer.status_code == 403 and "is not in a domain" in answer.json()["detail"]


async def test_nobody_is_seated_where_the_org_provisions_nobody(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp, members: MemoryMembers
) -> None:
    """The default: an address the org never invited is refused, and no row is made."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp, email=f"otra@{A_DOMAIN}")
    answer = await came_back(stranger, asked)
    assert answer.status_code == 403 and "nobody in" in answer.json()["detail"]
    assert len(await members.listed(AN_ORG.id)) == 1


async def test_an_invited_member_is_seated_by_signing_in_and_keeps_no_password(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp, members: MemoryMembers
) -> None:
    """A person who proved who they are at the org's provider has accepted their invitation."""
    invited = await members.invite(AN_ORG.id, f"ana@{A_DOMAIN}", "Ana", "qa", ())
    assert invited is not None and invited.member.status == "invited"
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp, email=f"ana@{A_DOMAIN}")
    assert (await came_back(stranger, asked)).status_code == 302
    kept = await members.by_email(AN_ORG.id, f"ana@{A_DOMAIN}")
    assert kept is not None and kept.member.status == "active" and kept.password_hash is None


async def test_an_unknown_address_is_seated_where_the_org_says_so(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp, members: MemoryMembers
) -> None:
    """Auto-provisioning: the role the org named, active at once, and a seat taken for it."""
    await wired(sso, role="qa")
    asked = await sent_to_the_provider(stranger, idp, email=f"otra@{A_DOMAIN}", name="Otra")
    assert (await came_back(stranger, asked)).status_code == 302
    kept = await members.by_email(AN_ORG.id, f"otra@{A_DOMAIN}")
    assert kept is not None and kept.member.role == "qa" and kept.member.status == "active"
    assert kept.member.name == "Otra" and await members.seated(AN_ORG.id) == 2


async def test_auto_provisioning_respects_the_seats_a_plan_allows(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp, orgs: MemoryOrgs, members: MemoryMembers
) -> None:
    """A seat is a seat whoever made it: the quota answers here in its own sentence."""
    await orgs.set_quotas(AN_ORG.id, Quotas(seats=1))
    await wired(sso, role="qa")
    asked = await sent_to_the_provider(stranger, idp, email=f"otra@{A_DOMAIN}")
    answer = await came_back(stranger, asked)
    assert answer.status_code == 429 and "seats" in answer.json()["detail"]
    assert len(await members.listed(AN_ORG.id)) == 1


async def test_a_disabled_member_signs_in_nowhere(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp, members: MemoryMembers
) -> None:
    """Disabling somebody closes the provider's way in too, or it would close nothing."""
    await members.update(AN_ORG.id, NICO.id, status="disabled")
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    answer = await came_back(stranger, asked)
    assert answer.status_code == 403 and "disabled" in answer.json()["detail"]


async def test_the_terminal_pairing_rides_along_untouched(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """`pinecall login` sends a person through SSO; they land back on the card to approve it."""
    await wired(sso)
    asked = await sent_to_the_provider(
        stranger, idp, asking=f"?org={AN_ORG.slug}&pairing=cli_a_word"
    )
    back = await came_back(stranger, asked)
    landed = httpx.URL(back.headers["location"])
    assert landed.path == THE_CARD and landed.params["c"] == "cli_a_word"
    assert str(landed.params["login"]).startswith("lc_")


async def test_an_org_that_signs_in_with_nobody_is_a_404(stranger: httpx.AsyncClient) -> None:
    answer = await stranger.get(f"{SIGN_IN}?org={AN_ORG.slug}")
    assert answer.status_code == 404 and "no identity provider" in answer.json()["detail"]


async def test_a_provider_that_refuses_the_exchange_is_said_in_its_own_words(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    """`invalid_client` and `access_denied` are two different afternoons: it says which."""
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    idp.refuses = "invalid_client"
    answer = await came_back(stranger, asked)
    assert answer.status_code == 401 and "invalid_client" in answer.json()["detail"]


async def test_a_person_who_said_no_at_the_provider_comes_back_with_the_reason(
    stranger: httpx.AsyncClient, sso: Sso, idp: FakeIdp
) -> None:
    await wired(sso)
    asked = await sent_to_the_provider(stranger, idp)
    answer = await stranger.get(f"{CALLBACK}?state={asked['state']}&error=access_denied")
    assert answer.status_code == 401 and "access_denied" in answer.json()["detail"]


# ── what a password does once the org has a provider ────────────────────────────


async def test_a_password_still_opens_an_org_that_only_offers_the_provider(
    stranger: httpx.AsyncClient, sso: Sso, members: MemoryMembers
) -> None:
    """Wiring a provider is not requiring it: both ways in until the org says otherwise."""
    email = await _with_a_password(members)
    await wired(sso)
    answer = await stranger.post("/v1/login", json={"email": email, "password": A_PASSWORD})
    assert answer.status_code == 200


async def test_a_required_provider_refuses_the_password_and_says_where_to_go(
    stranger: httpx.AsyncClient, sso: Sso, members: MemoryMembers
) -> None:
    """Said only once the password matched, so a stranger learns nothing about who is a member."""
    email = await _with_a_password(members)
    await wired(sso, required=True)
    answer = await stranger.post("/v1/login", json={"email": email, "password": A_PASSWORD})
    assert answer.status_code == 401
    assert answer.json()["detail"] == WITH_THE_PROVIDER.format(org=AN_ORG.slug)


async def test_a_wrong_password_at_a_required_org_is_the_one_401_it_always_was(
    stranger: httpx.AsyncClient, sso: Sso, members: MemoryMembers
) -> None:
    """The refusal that says where to sign in must not be a way to walk the box's members."""
    email = await _with_a_password(members)
    await wired(sso, required=True)
    answer = await stranger.post("/v1/login", json={"email": email, "password": "not-theirs"})
    assert answer.status_code == 401 and "identity provider" not in answer.json()["detail"]


async def _with_a_password(members: MemoryMembers) -> str:
    """One more person of the org, seated the way an invitation seats one: with a password."""
    invited = await members.invite(AN_ORG.id, f"conpass@{A_DOMAIN}", "Con Pass", "developer", ())
    assert invited is not None and invited.token is not None
    seated = await members.accept(invited.token, await passwords.hash_password(A_PASSWORD, 8))
    assert seated is not None
    return seated.email


# ── the sign-in page's discovery ────────────────────────────────────────────────


async def test_discovery_names_the_orgs_of_a_domain_and_nobody_of_them(
    stranger: httpx.AsyncClient, sso: Sso
) -> None:
    """A domain is a fact about the ORG; whether anybody answers to the address is not said."""
    await wired(sso)
    answer = await stranger.post("/v1/login/sso/discover", json={"email": f"NOBODY@{A_DOMAIN} "})
    assert answer.status_code == 200
    assert answer.json() == {"orgs": [{"org": AN_ORG.id, "slug": AN_ORG.slug, "name": AN_ORG.name}]}


async def test_discovery_says_nothing_about_a_domain_nobody_wired(
    stranger: httpx.AsyncClient, sso: Sso
) -> None:
    await wired(sso)
    answer = await stranger.post("/v1/login/sso/discover", json={"email": "nico@elsewhere.test"})
    assert answer.status_code == 200 and answer.json() == {"orgs": []}
