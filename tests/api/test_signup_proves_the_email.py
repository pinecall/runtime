"""A sign-up is only an org once the code mailed to it comes back: wrong, late, resent, keyed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.accounts.signup import NO_MAIL, NOT_THE_SHIELD, REFUSED, TOO_MANY
from pinecall.auth.signups import ATTEMPTS, CODE_TTL_S, PendingSignups
from pinecall.auth.throttle import TRIES_PER_WINDOW
from pinecall.mail.outbox import Outbox
from pinecall.mail.smtp import Mailbox
from pinecall.orgs.table import MemoryOrgs
from tests.api.accounts.test_signup import A_CODE, TIENDA, VERIFY, asked, the_code_mailed
from tests.api.conftest import A_LIVEKIT, A_VAULT_KEY, AN_OPS_KEY, over_the_asgi_app
from tests.api.mailing import A_BOX_SENDER
from tests.clocks import Clock
from tests.mail.fake_smtp import FakeSmtp

pytestmark = pytest.mark.unit

RESEND = "/v1/signup/resend"
THE_SHIELDS_KEY = "the-landing-holds-this-and-nobody-else"


@pytest.fixture
def clock() -> Clock:
    return Clock(1_000_000.0)


@pytest.fixture
def signups(clock: Clock) -> PendingSignups:
    """The pending sign-ups on this test's own clock, so fifteen minutes pass in one line."""
    return PendingSignups(clock)


@pytest.fixture
def settings() -> Settings:
    """A gateway whose operator opened sign-ups, with no shield's key: anybody knocks."""
    return Settings(
        world="production",
        ops_key=AN_OPS_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        vault_key=A_VAULT_KEY,
        signup=True,
    )


@pytest.fixture
def the_boxs_mail(relay: FakeSmtp) -> Mailbox:
    return relay.mailbox(sender=A_BOX_SENDER)


@pytest.fixture
async def stranger(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app("")
    yield http
    await http.aclose()


async def verified(stranger: httpx.AsyncClient, code: str, **changed: Any) -> httpx.Response:
    return await stranger.post(VERIFY, json={"email": TIENDA["email"], "code": code, **changed})


def keyed(settings: Settings) -> None:
    """This gateway, now behind a shield: the sign-up doors take its key and nobody else's."""
    from pinecall.api import deps
    from pinecall.api.app import app

    shielded = settings.model_copy(update={"signup_key": THE_SHIELDS_KEY})
    app.dependency_overrides[deps.a_settings] = lambda: shielded


# ── the letter, and nothing before the code ─────────────────────────────────────


async def test_asking_mails_a_code_and_makes_no_org_until_it_comes_back(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp, orgs: MemoryOrgs
) -> None:
    first = await asked(stranger)
    assert first.status_code == 202, first.text
    assert set(first.json()) == {"email", "code_expires_at"}, "the code is never in the answer"
    assert await orgs.find("tienda-sur") is None, "nobody proved the address yet"
    code = await the_code_mailed(outbox, relay)
    letter = relay.took[-1]
    assert list(letter.recipients) == [TIENDA["email"]]
    assert code not in letter.message["Subject"], "the outbox logs every subject"
    assert code in letter.parts["text/html"] and "http" not in letter.parts["text/plain"]
    made = await verified(stranger, code)
    assert made.status_code == 201, made.text
    assert await orgs.find("tienda-sur") is not None


async def test_a_code_is_spent_once(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    await asked(stranger)
    code = await the_code_mailed(outbox, relay)
    assert (await verified(stranger, code)).status_code == 201
    again = await verified(stranger, code)
    assert (again.status_code, again.json()["detail"]) == (400, REFUSED["wrong"])


# ── wrong, burned, late, unknown ─────────────────────────────────────────────────


async def test_six_wrong_codes_burn_it_and_the_right_one_is_refused_after(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp, orgs: MemoryOrgs
) -> None:
    from pinecall.api import deps
    from pinecall.api.app import app
    from pinecall.auth.throttle import Throttle

    # The throttle is its own rule (below); here every wrong try reaches the code.
    app.dependency_overrides[deps.the_throttle] = lambda: Throttle(tries=100)
    await asked(stranger)
    code = await the_code_mailed(outbox, relay)
    wrong = "000000" if code != "000000" else "111111"
    said = [(await verified(stranger, wrong)).json()["detail"] for _ in range(ATTEMPTS)]
    assert said == [REFUSED["wrong"]] * (ATTEMPTS - 1) + [REFUSED["burned"]]
    late = await verified(stranger, code)
    assert (late.status_code, late.json()["detail"]) == (400, REFUSED["burned"])
    assert await orgs.find("tienda-sur") is None


async def test_a_code_fifteen_minutes_old_has_expired(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp, clock: Clock
) -> None:
    await asked(stranger)
    code = await the_code_mailed(outbox, relay)
    clock.now += CODE_TTL_S
    late = await verified(stranger, code)
    assert (late.status_code, late.json()["detail"]) == (400, REFUSED["expired"])


async def test_a_code_for_an_address_nobody_signed_up_with_reads_as_a_wrong_one(
    stranger: httpx.AsyncClient,
) -> None:
    """The door never says whether an address has a sign-up waiting."""
    answer = await stranger.post(VERIFY, json={"email": "nadie@x.uy", "code": "123456"})
    assert (answer.status_code, answer.json()["detail"]) == (400, REFUSED["wrong"])


# ── resend ───────────────────────────────────────────────────────────────────────


async def test_a_resent_code_works_and_the_first_one_no_longer_does(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    await asked(stranger)
    first = await the_code_mailed(outbox, relay)
    resent = await stranger.post(RESEND, json={"email": TIENDA["email"]})
    assert (resent.status_code, resent.json()) == (202, {})
    second = await the_code_mailed(outbox, relay)
    assert len(relay.took) == 2
    if first != second:
        assert (await verified(stranger, first)).status_code == 400
    assert (await verified(stranger, second)).status_code == 201


async def test_a_resend_for_an_unknown_address_answers_the_same_and_sends_nothing(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp
) -> None:
    answer = await stranger.post(RESEND, json={"email": "nadie@x.uy"})
    assert (answer.status_code, answer.json()) == (202, {})
    await outbox.drained()
    assert relay.took == []


# ── a box that cannot write ──────────────────────────────────────────────────────


class TestABoxWithNoMail:
    @pytest.fixture
    def the_boxs_mail(self) -> Mailbox | None:
        return None

    async def test_takes_no_sign_up_and_keeps_nothing(
        self, stranger: httpx.AsyncClient, signups: PendingSignups
    ) -> None:
        answer = await asked(stranger)
        assert (answer.status_code, answer.json()["detail"]) == (503, NO_MAIL)
        assert signups.renewed(TIENDA["email"]) is None


# ── the slug ─────────────────────────────────────────────────────────────────────


async def test_a_slug_taken_while_the_code_travelled_is_refused_at_verify(
    stranger: httpx.AsyncClient, outbox: Outbox, relay: FakeSmtp, orgs: MemoryOrgs
) -> None:
    await asked(stranger)
    code = await the_code_mailed(outbox, relay)
    await orgs.create("tienda-sur", "Somebody faster")
    answer = await verified(stranger, code)
    assert answer.status_code == 409


# ── behind a shield ──────────────────────────────────────────────────────────────


async def test_with_a_shields_key_set_the_doors_take_that_key_and_nobody_else(
    stranger: httpx.AsyncClient, settings: Settings
) -> None:
    keyed(settings)
    for door, body in (
        ("/v1/signup", TIENDA),
        (VERIFY, {"email": TIENDA["email"], "code": "123456"}),
        (RESEND, {"email": TIENDA["email"]}),
    ):
        refused = await stranger.post(door, json=body)
        assert (refused.status_code, refused.json()["detail"]) == (401, NOT_THE_SHIELD), door
    taken = await stranger.post(
        "/v1/signup", json=TIENDA, headers={"Authorization": f"Bearer {THE_SHIELDS_KEY}"}
    )
    assert taken.status_code == 202, taken.text


async def test_behind_the_key_the_throttle_counts_the_address_the_shield_says(
    stranger: httpx.AsyncClient, settings: Settings
) -> None:
    """Two people behind one shield are two budgets, not one."""
    keyed(settings)
    as_the_shield = {"Authorization": f"Bearer {THE_SHIELDS_KEY}"}
    for n in range(TRIES_PER_WINDOW):
        said = {**TIENDA, "org": f"org-{n}", "email": f"p{n}@x.uy"}
        headers = {**as_the_shield, "X-Pinecall-Client": "203.0.113.7"}
        assert (await stranger.post("/v1/signup", json=said, headers=headers)).status_code == 202
    over = {**as_the_shield, "X-Pinecall-Client": "203.0.113.7"}
    refused = await stranger.post("/v1/signup", json={**TIENDA, "org": "one-more"}, headers=over)
    assert (refused.status_code, refused.json()["detail"]) == (429, TOO_MANY)
    another = {**as_the_shield, "X-Pinecall-Client": "198.51.100.9"}
    elsewhere = await stranger.post("/v1/signup", json=TIENDA, headers=another)
    assert elsewhere.status_code == 202, "another person, as the shield says"


async def test_without_a_key_the_shields_header_is_never_believed(
    stranger: httpx.AsyncClient,
) -> None:
    """Anybody can write the header: an open door throttles where the knock really came from."""
    for n in range(TRIES_PER_WINDOW):
        said = {**TIENDA, "org": f"org-{n}", "email": f"p{n}@x.uy"}
        faked = {"X-Pinecall-Client": f"203.0.113.{n}"}
        assert (await stranger.post("/v1/signup", json=said, headers=faked)).status_code == 202
    refused = await stranger.post(
        "/v1/signup",
        json={**TIENDA, "org": "one-more"},
        headers={"X-Pinecall-Client": "198.51.100.1"},
    )
    assert refused.status_code == 429


def test_the_code_pattern_matches_six_digits_alone() -> None:
    assert A_CODE.search("Confirm\n\n042917\n\nThis code") is not None
    assert A_CODE.search("call 0429171234") is None
