"""An invitation and a reset in somebody's inbox: which mail server carried it, and what it said."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.org.mail import NO_MAIL, NOTHING_TO_TEST
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.mail import Outbox
from pinecall.orgs.mail import Mail
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Mailbox, Org
from tests.api.conftest import A_KEY, A_RECORD, AN_ORG, over_the_asgi_app
from tests.api.mailing import A_BOX_SENDER, AN_ORGS_SENDER
from tests.api.no_vault import WithNoVaultKey
from tests.api.test_members_and_login import BERNA, accepted, invited
from tests.mail.fake_smtp import NOT_AUTHORIZED, FakeSmtp

pytestmark = pytest.mark.unit

THE_DOOR = "/v1/org/mail"
A_PASSWORD_NOBODY_MAY_READ = "an-ses-smtp-password"
ANOTHER_ORG = Org(id="tienda", slug="tienda-sur", name="Tienda Sur")
# The key the tenant's doors are opened with, with a name on it: an invitation says who invited.
ANA = KeyRecord(key_id=A_RECORD.key_id, org=A_RECORD.org, label="console", name="Ana Vidal")


@pytest.fixture
def keys() -> MemoryKeys:
    """One key, and it names a person: what a member logging in at the console holds."""
    return MemoryKeys({A_KEY: ANA})


@pytest.fixture
def the_boxs_mail(relay: FakeSmtp) -> Mailbox:
    """This box has a mail server of its own: every org's letters go through it by default."""
    return relay.mailbox(sender=A_BOX_SENDER)


def wiring(server: FakeSmtp) -> dict[str, object]:
    """What an admin PUTs to send an org's letters through that server, with a password in it."""
    return {
        "host": server.host,
        "port": server.port,
        "security": "none",
        "username": "AKIAEXAMPLE",
        "password": A_PASSWORD_NOBODY_MAY_READ,
        "from": AN_ORGS_SENDER,
    }


# ── the letters themselves ──────────────────────────────────────────────────────


async def test_an_invitation_is_mailed_and_carries_the_very_token_the_answer_carries(
    tenant_http: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    """The answer keeps the token exactly as it did, and gains one word about the letter."""
    said = await invited(tenant_http)
    assert said["mailed"] is True and said["token"].startswith("inv_")
    await outbox.drained()
    letter = relay.took[0]
    assert letter.recipients == (BERNA["email"],)
    assert letter.message["From"] == A_BOX_SENDER
    text = letter.parts["text/plain"]
    # The org, who invited them, and the console's own card at the name this gateway answers to.
    assert "Clínica Norte" in letter.message["Subject"] and "Ana Vidal" in text
    assert f"http://gateway.test/invitations/{said['token']}" in text


async def test_an_admins_reset_is_mailed_to_the_member_and_says_who_reset_it(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    """The one-use link is still in the answer; it is now also in the person's inbox."""
    member = (await accepted(stranger, (await invited(tenant_http))["token"]))["member"]
    answer = await tenant_http.post(f"/v1/members/{member['id']}/reset")
    assert answer.status_code == 201 and answer.json()["mailed"] is True
    await outbox.drained()
    reset = relay.took[-1]
    assert reset.recipients == (BERNA["email"],)
    assert f"http://gateway.test/invitations/{answer.json()['token']}" in reset.parts["text/plain"]
    assert "Ana Vidal" in reset.parts["text/plain"]


async def test_an_org_that_wired_its_own_mail_never_posts_through_the_boxs(
    tenant_http: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp, the_orgs_relay: FakeSmtp
) -> None:
    """Two servers are up; which one took the letter is what this reads, and it is the org's."""
    assert (await tenant_http.put(THE_DOOR, json=wiring(the_orgs_relay))).status_code == 200
    await invited(tenant_http)
    await outbox.drained()
    assert relay.took == []
    assert len(the_orgs_relay.took) == 1
    assert the_orgs_relay.took[0].message["From"] == AN_ORGS_SENDER


async def test_a_sign_in_page_is_told_the_box_can_mail_before_it_holds_a_key(
    stranger: httpx.AsyncClient,
) -> None:
    """So "Forgot your password?" may promise an email rather than "ask an admin"."""
    assert (await stranger.get("/.well-known/pinecall")).json()["mail"] is True


class TestWithNoMailAnywhere:
    """A box that was told nothing and an org that wired nothing: the gateway before mail."""

    @pytest.fixture
    def the_boxs_mail(self) -> Mailbox | None:
        return None

    async def test_a_sign_in_page_is_told_there_is_no_mail(
        self, stranger: httpx.AsyncClient
    ) -> None:
        assert (await stranger.get("/.well-known/pinecall")).json()["mail"] is False

    async def test_the_answers_are_what_they_always_were_and_mailed_is_false(
        self, tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, outbox: Outbox
    ) -> None:
        said = await invited(tenant_http)
        assert said["mailed"] is False and said["token"].startswith("inv_")
        member = (await accepted(stranger, said["token"]))["member"]
        reset = await tenant_http.post(f"/v1/members/{member['id']}/reset")
        assert reset.json()["mailed"] is False and reset.json()["token"].startswith("inv_")
        await outbox.drained()

    async def test_a_test_send_is_refused_because_there_is_nothing_to_test(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        answer = await tenant_http.post(f"{THE_DOOR}/test", json={"to": "ana@clinica.uy"})
        assert answer.status_code == 409 and answer.json()["detail"] == NOTHING_TO_TEST


# ── what came of it, where an admin reads it ────────────────────────────────────


async def test_a_refused_letter_is_recorded_on_the_orgs_own_row_and_read_back_there(
    tenant_http: httpx.AsyncClient, outbox: Outbox
) -> None:
    """The send happens after the door answered, so the row is the only place to look."""
    async with FakeSmtp(refuses_the_letter=True) as refusing:
        await tenant_http.put(THE_DOOR, json=wiring(refusing))
        said = await invited(tenant_http)
        # Handed over is all `mailed` ever claims: what became of it is below.
        assert said["mailed"] is True
        await outbox.drained()
    standing = (await tenant_http.get(THE_DOOR)).json()
    assert NOT_AUTHORIZED in standing["last_error"] and standing["verified_at"] is None
    assert A_PASSWORD_NOBODY_MAY_READ not in (standing["last_error"] or "")


async def test_a_letter_that_went_through_dates_the_row_and_clears_the_error(
    tenant_http: httpx.AsyncClient, the_orgs_relay: FakeSmtp
) -> None:
    """The test send is the one door that waits, and it records exactly what a real letter does."""
    await tenant_http.put(THE_DOOR, json=wiring(the_orgs_relay))
    answer = await tenant_http.post(f"{THE_DOOR}/test", json={"to": "ana@clinica.uy"})
    assert answer.status_code == 200 and answer.json() == {"sent": True, "error": None}
    standing = (await tenant_http.get(THE_DOOR)).json()
    assert standing["verified_at"] is not None and standing["last_error"] is None
    assert the_orgs_relay.took[-1].recipients == ("ana@clinica.uy",)


async def test_a_test_send_says_what_the_server_said_rather_than_failing_the_door(
    tenant_http: httpx.AsyncClient,
) -> None:
    """A person is watching this one: the refusal is the answer, not a 500."""
    async with FakeSmtp(refuses_the_password=True) as refusing:
        await tenant_http.put(THE_DOOR, json=wiring(refusing))
        answer = await tenant_http.post(f"{THE_DOOR}/test", json={"to": "ana@clinica.uy"})
    assert answer.status_code == 200 and answer.json()["sent"] is False
    assert "535" in answer.json()["error"]
    assert A_PASSWORD_NOBODY_MAY_READ not in answer.text


# ── the wiring, and who may read it ─────────────────────────────────────────────


async def test_the_password_goes_in_and_never_comes_out(
    tenant_http: httpx.AsyncClient, the_orgs_relay: FakeSmtp, mail: Mail
) -> None:
    """The same shape the identity provider's client secret has, and for the same reason."""
    put = await tenant_http.put(THE_DOOR, json=wiring(the_orgs_relay))
    assert put.status_code == 200 and A_PASSWORD_NOBODY_MAY_READ not in put.text
    read = await tenant_http.get(THE_DOOR)
    assert A_PASSWORD_NOBODY_MAY_READ not in read.text
    assert read.json()["configured"] is True and read.json()["from"] == AN_ORGS_SENDER
    assert read.json()["username"] == "AKIAEXAMPLE"
    kept = await mail.of(AN_ORG.id)
    assert kept is not None and kept.mailbox.password == A_PASSWORD_NOBODY_MAY_READ


async def test_an_unwired_org_answers_one_shape_with_every_field_empty(
    tenant_http: httpx.AsyncClient,
) -> None:
    """One envelope either way, so a page parses one thing (protocol/schema/rest.json, OrgMail)."""
    assert (await tenant_http.get(THE_DOOR)).json() == {
        "configured": False,
        "host": None,
        "port": None,
        "security": None,
        "username": None,
        "from": None,
        "verified_at": None,
        "last_error": None,
    }


async def test_unwiring_twice_is_a_404_the_second_time(
    tenant_http: httpx.AsyncClient, the_orgs_relay: FakeSmtp
) -> None:
    await tenant_http.put(THE_DOOR, json=wiring(the_orgs_relay))
    assert (await tenant_http.delete(THE_DOOR)).status_code == 204
    gone = await tenant_http.delete(THE_DOOR)
    assert gone.status_code == 404 and gone.json()["detail"] == NO_MAIL


@pytest.mark.parametrize(
    "wrong", [{"security": "carrier-pigeon"}, {"port": 0}, {"from": "Pinecall"}, {"host": "a b"}]
)
async def test_a_mailbox_that_is_not_one_is_refused_and_nothing_is_kept(
    tenant_http: httpx.AsyncClient, the_orgs_relay: FakeSmtp, wrong: dict[str, object]
) -> None:
    answer = await tenant_http.put(THE_DOOR, json={**wiring(the_orgs_relay), **wrong})
    assert answer.status_code == 400
    assert (await tenant_http.get(THE_DOOR)).json()["configured"] is False


class TestTwoTenants:
    """One org's mail is not another's, on a box where both are wired and both are up."""

    @pytest.fixture
    def orgs(self) -> MemoryOrgs:
        return MemoryOrgs([AN_ORG, ANOTHER_ORG])

    @pytest.fixture
    def keys(self) -> MemoryKeys:
        return MemoryKeys(
            {A_KEY: ANA, "pk_test_the_shop": KeyRecord(key_id="k_2", org=ANOTHER_ORG.id)}
        )

    async def test_what_one_org_wired_is_invisible_and_unused_next_door(
        self, tenant_http: httpx.AsyncClient, outbox: Outbox, the_orgs_relay: FakeSmtp
    ) -> None:
        await tenant_http.put(THE_DOOR, json=wiring(the_orgs_relay))
        async with over_the_asgi_app("Bearer pk_test_the_shop") as neighbour:
            theirs = await neighbour.get(THE_DOOR)
            assert theirs.status_code == 200 and theirs.json()["configured"] is False
            await neighbour.post("/v1/members", json={**BERNA, "email": "nico@tiendasur.uy"})
        await outbox.drained()
        # The neighbour's invitation went through the BOX's mail, never through this org's.
        assert [one.recipients for one in the_orgs_relay.took] == []


class TestWithNoVaultKey(WithNoVaultKey):
    """A runtime given no PINECALL_VAULT_KEY keeps nobody's password, and says so once."""

    door = THE_DOOR

    @pytest.fixture
    def mail(self) -> Mail | None:
        return None

    async def test_the_boxs_own_mail_still_carries_an_invitation(
        self, tenant_http: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
    ) -> None:
        """The box's mail is the BOX's credential, not a tenant's: no vault is needed to read it."""
        assert (await invited(tenant_http))["mailed"] is True
        await outbox.drained()
        assert len(relay.took) == 1
