"""POST /v1/login/reset: one answer for everybody, a letter for the few, and a token for fewer."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.forgot import ACCEPTED
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.throttle import TRIES_PER_WINDOW
from pinecall.mail import Outbox
from pinecall.orgs.sso import Sso
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Mailbox, Org, OrgSso
from tests.api.conftest import AN_ORG
from tests.api.mailing import A_BOX_SENDER
from tests.api.test_members_and_login import A_PASSWORD, BERNA, LOGIN, accepted, invited
from tests.mail.fake_smtp import FakeSmtp

pytestmark = pytest.mark.unit

THE_DOOR = "/v1/login/reset"
A_NEW_PASSWORD = "a much better horse battery staple"
NOBODY = "nobody@nowhere.uy"
# The invited member's address as a str: the fixture is a body, so its values are wider.
BERNAS = str(BERNA["email"])


@pytest.fixture
def the_boxs_mail(relay: FakeSmtp) -> Mailbox:
    """This box can post a letter, which is the only condition under which one is minted."""
    return relay.mailbox(sender=A_BOX_SENDER)


# The invitation is a letter too, so it is drained and forgotten before the case begins: what
# `relay.took` holds after this is what the door under test posted, and nothing else.
async def a_member(
    tenant_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    outbox: Outbox,
    relay: FakeSmtp,
) -> None:
    """One person of the org, invited and accepted, with a password of their own."""
    await accepted(stranger, (await invited(tenant_http))["token"])
    await outbox.drained()
    relay.took.clear()


async def asked(stranger: httpx.AsyncClient, email: str) -> httpx.Response:
    """What anybody at the sign-in page does: type an address and press the button."""
    return await stranger.post(THE_DOOR, json={"email": email})


async def test_the_link_reaches_the_person_and_opens_the_card_an_invitation_opens(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    """The whole point: nobody has to ask an admin, and the token is never in the answer."""
    await a_member(tenant_http, stranger, outbox, relay)
    answer = await asked(stranger, BERNAS)
    assert (answer.status_code, answer.json()) == (ACCEPTED, {})
    await outbox.drained()
    letter = relay.took[-1]
    assert letter.recipients == (BERNAS,)
    token = letter.parts["text/plain"].split("/invitations/")[1].split()[0]
    assert token.startswith("inv_")
    await accepted(stranger, token, password=A_NEW_PASSWORD)
    old = await stranger.post(LOGIN, json={"email": BERNAS, "password": A_PASSWORD})
    new = await stranger.post(LOGIN, json={"email": BERNAS, "password": A_NEW_PASSWORD})
    assert (old.status_code, new.status_code) == (401, 200)


async def test_an_address_nobody_answers_to_reads_exactly_like_one_that_somebody_does(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    """A door that told them apart would be a box's directory, read one address at a time."""
    await a_member(tenant_http, stranger, outbox, relay)
    known = await asked(stranger, BERNAS)
    unknown = await asked(stranger, NOBODY)
    assert (known.status_code, known.text) == (unknown.status_code, unknown.text)
    await outbox.drained()
    assert [letter.recipients for letter in relay.took] == [(BERNAS,)]


async def test_the_sixth_try_in_a_minute_is_the_logins_own_refusal(
    stranger: httpx.AsyncClient,
) -> None:
    """It shares /v1/login's count, so walking a list of addresses is stopped where one is."""
    for _ in range(TRIES_PER_WINDOW):
        assert (await asked(stranger, NOBODY)).status_code == ACCEPTED
    stopped = await asked(stranger, NOBODY)
    assert stopped.status_code == 429 and "try again in a minute" in stopped.json()["detail"]


async def test_a_member_still_invited_is_not_reset_because_their_invitation_is_the_link(
    tenant_http: httpx.AsyncClient, stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    """The same rule the admin's reset follows, and the same silence about which it was."""
    pending = await invited(tenant_http)
    await outbox.drained()
    relay.took.clear()
    assert (await asked(stranger, BERNAS)).status_code == ACCEPTED
    await outbox.drained()
    assert relay.took == []
    # …and the invitation they were sent still opens: nothing spent it.
    await accepted(stranger, pending["token"])


class TestWhereNobodyCanSendAnything:
    """A box with no mail and an org with none: the door answers the same and mints nothing."""

    @pytest.fixture
    def the_boxs_mail(self) -> Mailbox | None:
        return None

    async def test_the_answer_is_the_same_202_and_no_link_is_burned(
        self,
        tenant_http: httpx.AsyncClient,
        stranger: httpx.AsyncClient,
        members: MemoryMembers,
        outbox: Outbox,
    ) -> None:
        """A token minted with nothing to carry it would let a stranger kill an admin's link."""
        member = (await accepted(stranger, (await invited(tenant_http))["token"]))["member"]
        handed = (await tenant_http.post(f"/v1/members/{member['id']}/reset")).json()["token"]
        assert (await asked(stranger, BERNAS)).status_code == ACCEPTED
        await outbox.drained()
        assert await members.find(AN_ORG.id, member["id"]) is not None
        # The admin's link is still the newest one, because this door minted none.
        assert (
            await stranger.post(f"/v1/invitations/{handed}", json={"password": A_NEW_PASSWORD})
        ).status_code == 200


class TestAnOrgThatSignsInWithItsProvider:
    """`required` is the org saying a password opens it no longer, so there is none to reset."""

    async def test_no_letter_is_posted_and_the_answer_is_the_same(
        self,
        tenant_http: httpx.AsyncClient,
        stranger: httpx.AsyncClient,
        sso: Sso,
        outbox: Outbox,
        relay: FakeSmtp,
    ) -> None:
        await a_member(tenant_http, stranger, outbox, relay)
        await sso.put(
            OrgSso(
                org=AN_ORG.id,
                issuer="https://idp.test",
                client_id="client",
                client_secret="secret",
                domains=("clinica.uy",),
                required=True,
            )
        )
        assert (await asked(stranger, BERNAS)).status_code == ACCEPTED
        await outbox.drained()
        assert relay.took == []


class TestAPersonOfTwoOrgs:
    """The oldest org of theirs that a password still opens and whose letters can carry a link."""

    @pytest.fixture
    def orgs(self) -> MemoryOrgs:
        return MemoryOrgs([AN_ORG, Org(id="tienda", slug="tienda-sur", name="Tienda Sur")])

    async def test_one_letter_and_not_two(
        self,
        tenant_http: httpx.AsyncClient,
        stranger: httpx.AsyncClient,
        members: MemoryMembers,
        outbox: Outbox,
        relay: FakeSmtp,
    ) -> None:
        await a_member(tenant_http, stranger, outbox, relay)
        await members.invite("tienda", BERNAS, "Berna", "admin", [])
        assert (await asked(stranger, BERNAS)).status_code == ACCEPTED
        await outbox.drained()
        assert len(relay.took) == 1
        assert "Clínica Norte" in relay.took[0].parts["text/plain"]
