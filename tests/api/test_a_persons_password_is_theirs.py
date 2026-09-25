"""One password across the orgs means one org's admin never holds the link that sets it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.signup import ALREADY_INVITED
from pinecall.auth.keys import MemoryKeys
from pinecall.orgs.table import MemoryOrgs
from tests.api.conftest import A_LIVEKIT, A_RECORD, A_VAULT_KEY, AN_OPS_KEY, over_the_asgi_app

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"
A_PASSWORD = "correct horse battery staple"
JP = {"email": "jp@cloudacio.com", "name": "JP", "role": "developer"}


@pytest.fixture
def settings() -> Settings:
    """Sign-ups open, so the stranger's door is one of the three tried here."""
    return Settings(
        world="production",
        ops_key=AN_OPS_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        vault_key=A_VAULT_KEY,
        signup=True,
    )


@pytest.fixture
async def another_admin(
    wired: None,  # noqa: ARG001
    orgs: MemoryOrgs,
    keys: MemoryKeys,
) -> AsyncIterator[httpx.AsyncClient]:
    """A SECOND tenant on this box, knocking with that org's own key."""
    other = await orgs.create("cloudacio", "Cloudacio")
    assert other is not None
    issued = await keys.issue(other.id, "their console")
    http = over_the_asgi_app(f"Bearer {issued.key}")
    yield http
    await http.aclose()


async def invited_by(http: httpx.AsyncClient, **changed: Any) -> dict[str, Any]:
    answer = await http.post(MEMBERS, json={**JP, **changed})
    assert answer.status_code == 201, answer.text
    return answer.json()


async def test_the_link_for_somebody_invited_elsewhere_is_never_handed_to_this_orgs_admin(
    tenant_http: httpx.AsyncClient, another_admin: httpx.AsyncClient
) -> None:
    """JP is pending at the clinic. The other org's admin may seat JP — and may not hold the
    link that would choose JP's password, which is the password the clinic would then seat."""
    pending = await invited_by(tenant_http)
    assert pending["token"] is not None, "the clinic's own: nobody else has this address"
    theirs = await invited_by(another_admin)
    assert theirs["member"]["status"] == "invited"
    assert theirs["token"] is None, "posted to JP, and to nobody else"
    assert theirs["mailed"] is False, "this box sends no mail: the answer says so, and lies not"


async def test_a_reset_for_somebody_of_two_orgs_is_never_handed_to_one_of_them(
    tenant_http: httpx.AsyncClient,
    ops_http: httpx.AsyncClient,
    another_admin: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
) -> None:
    """Once JP has a password, a reset from the other org would replace it everywhere."""
    # The operator's link vouches for the address, so the second org seats JP at once (0048).
    pending = (await ops_http.post(f"/v1/ops/orgs/{A_RECORD.org}/members", json=JP)).json()
    accepted = await stranger.post(
        f"/v1/invitations/{pending['token']}", json={"password": A_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["member"]["verified"] is True
    # JP is the clinic's alone: its admin is handed the link, as before.
    alone = await tenant_http.post(f"{MEMBERS}/{pending['member']['id']}/reset")
    assert alone.status_code == 201 and alone.json()["token"] is not None
    seated = await invited_by(another_admin)
    assert seated["member"]["status"] == "active" and seated["token"] is None
    # JP is two orgs' now: NEITHER admin is handed a link that would set JP's one password.
    for admin, member in ((another_admin, seated), (tenant_http, pending)):
        reset = await admin.post(f"{MEMBERS}/{member['member']['id']}/reset")
        assert reset.status_code == 201, reset.text
        assert reset.json()["token"] is None, "posted to JP; no org's admin holds it"
    # And JP's password is still JP's.
    signed = await stranger.post("/v1/login", json={"email": JP["email"], "password": A_PASSWORD})
    assert signed.status_code == 200, signed.text


async def test_whoever_chose_an_addresss_password_first_is_seated_nowhere_it_is_invited_next(
    tenant_http: httpx.AsyncClient,
    ops_http: httpx.AsyncClient,
    another_admin: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
) -> None:
    """Pre-registration: the rival invites an address nobody has yet, accepts the handed link
    with a password of THEIR choosing, and waits for the clinic to invite that address."""
    squatted = await invited_by(another_admin)
    assert squatted["token"] is not None, "nobody else's yet: handed over, and vouching for nobody"
    taken = await stranger.post(
        f"/v1/invitations/{squatted['token']}", json={"password": "the rival's choosing"}
    )
    assert taken.status_code == 200 and taken.json()["member"]["verified"] is False

    # The clinic invites the real JP: not seated on the rival's password — invited, like anybody.
    here = await invited_by(tenant_http)
    assert here["member"]["status"] == "invited" and here["token"] is None
    # And the rival, knowing the password, is not seated in the clinic at login either.
    walked_in = await stranger.post(
        "/v1/login",
        json={"org": A_RECORD.org, "email": JP["email"], "password": "the rival's choosing"},
    )
    assert walked_in.status_code == 403, walked_in.text
    assert [row["status"] for row in (await tenant_http.get(MEMBERS)).json()["members"]] == [
        "invited"
    ]
    # The operator's link reaches the real JP, who chooses a password: the rival's is gone
    # everywhere, and the rival's row is now JP's.
    vouched = (await ops_http.post(f"/v1/ops/orgs/{A_RECORD.org}/members", json=JP)).json()
    theirs = await stranger.post(
        f"/v1/invitations/{vouched['token']}", json={"password": A_PASSWORD}
    )
    assert theirs.status_code == 200, theirs.text
    locked_out = await stranger.post(
        "/v1/login",
        json={"org": "cloudacio", "email": JP["email"], "password": "the rival's choosing"},
    )
    assert locked_out.status_code == 401


async def test_a_sign_up_cannot_choose_the_password_of_somebody_invited_elsewhere(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    pending = await invited_by(tenant_http)
    taken = await stranger.post(
        "/v1/signup",
        json={
            "org": "rival",
            "email": JP["email"],
            "person": "Somebody",
            "password": "a password of somebody else's choosing",
        },
    )
    assert taken.status_code == 409, taken.text
    assert taken.json()["detail"] == ALREADY_INVITED.format(email=JP["email"])
    # Nothing was made: JP's row is the clinic's alone, still invited, still passwordless.
    listed = (await tenant_http.get(MEMBERS)).json()["members"]
    assert [row["status"] for row in listed] == ["invited"]
    assert pending["member"]["id"] == listed[0]["id"]
