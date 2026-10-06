"""Tests for sign-up: a code mailed, the org made only when it comes back, resent, shielded."""

import re
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest

from pinecall.domain.person import ROLE_SCOPES
from pinecall.gateway.app import app
from pinecall.tenancy import keys, orgs
from pinecall.tenancy.signin import ATTEMPTS, CODE_TTL_S, TRIES, SignIns
from pinecall.tenancy.words import Words
from tests.conftest import Knocking, postgres
from tests.fakes.mail import Postbox
from tests.gateway.api.conftest import box_can_mail, delivered, text_of

SIGNUP = "/v1/signup"
VERIFY = "/v1/signup/verify"
RESEND = "/v1/signup/resend"
WHAT_THEY_TYPE = "correct horse battery staple"
TIENDA = {
    "org": "tienda-sur",
    "name": "Tienda Sur",
    "email": "ana@tiendasur.test",
    "person": "Ana",
    "password": WHAT_THEY_TYPE,
}
TOO_SHORT = "short"
NOT_HERS = "not her password at all"
THE_SHIELDS_KEY = "the-landing-holds-this-and-nobody-else"
# Six digits alone on their line, as the letter's text writes the code.
A_CODE = re.compile(r"^(\d{6})$", re.MULTILINE)


def opened(knocking: Knocking, clock: Callable[[], float] | None = None, **update: object) -> None:
    """The same gateway with sign-ups open, and what else is said, on its own clock if given."""
    settings = knocking.gateway.connections.settings.model_copy(update={"signup": True, **update})
    gateway = replace(
        knocking.gateway, connections=replace(knocking.gateway.connections, settings=settings)
    )
    if clock is not None:
        words = Words(gateway.connections.pool, gateway.connections.vault, clock)
        gateway = replace(gateway, signins=SignIns.kept(words))
    app.state.gateway = gateway


def stranger(knocking: Knocking) -> httpx.AsyncClient:
    """A visitor on the landing page, holding no key."""
    return httpx.AsyncClient(base_url=knocking.url)


async def code_mailed(knocking: Knocking, postbox: Postbox) -> str:
    """The six digits of the newest letter."""
    await delivered(knocking, postbox)
    found = A_CODE.search(text_of(postbox.sent[-1]))
    assert found is not None, "the letter carries no code"
    return found.group(1)


async def signed_up(
    knocking: Knocking, postbox: Postbox, page: httpx.AsyncClient, **changed: object
) -> httpx.Response:
    """A sign-up asked for and confirmed with the code mailed."""
    first = await page.post(SIGNUP, json={**TIENDA, **changed})
    if first.status_code != 202:
        return first
    code = await code_mailed(knocking, postbox)
    return await page.post(VERIFY, json={"email": first.json()["email"], "code": code})


@pytest.fixture
async def mailing(knocking: Knocking, postbox: Postbox) -> Postbox:
    """A box that mails, with sign-ups open."""
    await box_can_mail(knocking)
    opened(knocking)
    return postbox


# ── the code, and no org before it ──


@postgres
async def test_asking_mails_a_code_and_makes_no_org_until_it_comes_back(
    knocking: Knocking, mailing: Postbox
) -> None:
    pool = knocking.gateway.connections.pool
    async with stranger(knocking) as page:
        first = await page.post(SIGNUP, json=TIENDA)
        assert first.status_code == 202, first.text
        assert set(first.json()) == {"email", "code_expires_at"}, "never the code"
        assert await orgs.find(pool, "tienda-sur") is None
        code = await code_mailed(knocking, mailing)
        letter = mailing.sent[-1]
        made = await page.post(VERIFY, json={"email": TIENDA["email"], "code": code})
        spent = await page.post(VERIFY, json={"email": TIENDA["email"], "code": code})
    assert str(letter["To"]) == TIENDA["email"]
    assert code not in str(letter["Subject"]), "the outbox logs every subject"
    assert made.status_code == 201, made.text
    assert await orgs.find(pool, "tienda-sur") is not None
    assert (spent.status_code, spent.json()["detail"]) == (400, "that code is not valid")


@postgres
async def test_the_org_is_made_with_its_admin_and_the_first_key_handed_over(
    knocking: Knocking, mailing: Postbox
) -> None:
    pool = knocking.gateway.connections.pool
    async with stranger(knocking) as page:
        answer = await signed_up(knocking, mailing, page)
    body = answer.json()
    org = await orgs.find(pool, "tienda-sur")
    assert org is not None
    assert org.name == "Tienda Sur"
    assert (body["org"], body["slug"], body["label"]) == (org.id, "tienda-sur", "signup")
    assert body["scopes"] == sorted(ROLE_SCOPES["admin"])
    assert (body["member"]["role"], body["member"]["status"]) == ("admin", "active")
    assert (body["subject"], body["name"]) == (body["member"]["id"], "Ana")
    assert body["code"].startswith("lc_")
    assert await keys.verify(pool, body["key"]) is not None
    assert WHAT_THEY_TYPE not in answer.text


@postgres
async def test_the_code_logs_a_browser_in_and_the_password_logs_the_person_in_after(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with stranger(knocking) as page:
        body = (await signed_up(knocking, mailing, page)).json()
        browser = await page.post("/v1/login", json={"code": body["code"], "device": "chrome"})
        later = await page.post(
            "/v1/login",
            json={"org": "tienda-sur", "email": TIENDA["email"], "password": WHAT_THEY_TYPE},
        )
    assert browser.status_code == 200
    assert browser.json()["key"] != body["key"]
    assert later.status_code == 200


# ── refusals ──


@postgres
async def test_a_box_nobody_opened_sign_ups_on_takes_none(knocking: Knocking) -> None:
    async with stranger(knocking) as page:
        shut = await page.post(SIGNUP, json=TIENDA)
        info = (await page.get("/.well-known/pinecall")).json()
    assert shut.status_code == 404
    assert "PINECALL_SIGNUP" in shut.json()["detail"]
    assert info["signup"] is False
    assert await orgs.find(knocking.gateway.connections.pool, "tienda-sur") is None


@postgres
async def test_a_box_that_cannot_mail_takes_no_sign_up_and_keeps_nothing(
    knocking: Knocking,
) -> None:
    opened(knocking)
    async with stranger(knocking) as page:
        answer = await page.post(SIGNUP, json=TIENDA)
        info = (await page.get("/.well-known/pinecall")).json()
    assert answer.status_code == 503
    assert "PINECALL_SMTP_URL" in answer.json()["detail"]
    assert info["signup"] is True
    assert await app.state.gateway.signins.signups.renewed(TIENDA["email"]) is None


@postgres
async def test_the_refusals_are_sentences_and_a_refused_sign_up_makes_no_org(
    knocking: Knocking, mailing: Postbox
) -> None:
    pool = knocking.gateway.connections.pool
    async with stranger(knocking) as page:
        assert (await signed_up(knocking, mailing, page)).status_code == 201
        taken = await signed_up(knocking, mailing, page, email="otra@tiendasur.test")
        bad_slug = await signed_up(knocking, mailing, page, org="Tienda Sur")
        short = await signed_up(knocking, mailing, page, org="corta", password=TOO_SHORT)
        no_email = await signed_up(knocking, mailing, page, org="sin-mail", email="ana")
    assert taken.status_code == 409
    assert "tienda-sur is taken" in taken.json()["detail"]
    assert bad_slug.status_code == 400
    assert "lowercase" in bad_slug.json()["detail"]
    assert short.status_code == 400
    assert "characters" in short.json()["detail"]
    assert no_email.status_code == 400
    assert "@" in no_email.json()["detail"]
    assert [org.slug for org in await orgs.listed(pool)][-1] == "tienda-sur", "nothing half-made"


# The page answers 202 alike: whether an address has an account is told to its mailbox alone.
@postgres
async def test_a_sign_up_naming_somebody_elses_email_mails_them_and_their_own_password_works(
    knocking: Knocking, mailing: Postbox
) -> None:
    pool = knocking.gateway.connections.pool
    async with stranger(knocking) as page:
        assert (await signed_up(knocking, mailing, page)).status_code == 201
        stolen = await page.post(SIGNUP, json={**TIENDA, "org": "otra-org", "password": NOT_HERS})
        await delivered(knocking, mailing)
        notice = text_of(mailing.sent[-1])
        second = await signed_up(
            knocking, mailing, page, org="tienda-norte", email=" ANA@tiendasur.test "
        )
    assert stolen.status_code == 202
    assert stolen.json()["email"] == TIENDA["email"]
    assert "already has an account" in notice
    assert A_CODE.search(notice) is None, "no code"
    assert await orgs.find(pool, "otra-org") is None
    assert second.status_code == 201, second.text


@postgres
async def test_an_address_invited_somewhere_is_answered_alike_and_mailed_to_accept_first(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        await console.post(
            "/v1/members", json={"email": TIENDA["email"], "name": "Ana", "role": "qa"}
        )
    async with stranger(knocking) as page:
        answered = await page.post(SIGNUP, json=TIENDA)
    assert answered.status_code == 202
    assert await delivered(knocking, mailing) == [TIENDA["email"]] * 2, "the invitation, a notice"
    notice = text_of(mailing.sent[-1])
    assert "Accept that invitation first" in notice
    assert A_CODE.search(notice) is None, "no code"


@postgres
async def test_the_sixth_sign_up_from_one_place_in_a_minute_is_throttled(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with stranger(knocking) as page:
        made = [
            (await page.post(SIGNUP, json={**TIENDA, "org": f"org-{n}", "email": f"p{n}@x.test"}))
            for n in range(TRIES)
        ]
        sixth = await page.post(SIGNUP, json={**TIENDA, "org": "org-more", "email": "m@x.test"})
    assert [item.status_code for item in made] == [202] * TRIES
    assert (sixth.status_code, sixth.json()["detail"]) == (
        429,
        "too many sign-ups from here: try again in a minute",
    )
    assert len(await delivered(knocking, mailing)) == TRIES


# ── wrong, late, resent ──


@postgres
async def test_six_wrong_codes_burn_it_and_the_right_one_is_refused_after(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    # The throttle's window passes between two knocks, so every try reaches the code.
    minutes = iter(range(0, 1_000_000, 61))
    opened(knocking, clock=lambda: float(next(minutes)))
    async with stranger(knocking) as page:
        await page.post(SIGNUP, json=TIENDA)
        code = await code_mailed(knocking, postbox)
        wrong = "000000" if code != "000000" else "111111"
        details = [
            (await page.post(VERIFY, json={"email": TIENDA["email"], "code": wrong})).json()
            for _ in range(ATTEMPTS)
        ]
        late = await page.post(VERIFY, json={"email": TIENDA["email"], "code": code})
    assert [item["detail"] for item in details][-1] == "too many tries: ask for a new code"
    assert (late.status_code, late.json()["detail"]) == (400, "too many tries: ask for a new code")
    assert await orgs.find(knocking.gateway.connections.pool, "tienda-sur") is None


@postgres
async def test_a_code_fifteen_minutes_old_has_expired(knocking: Knocking, postbox: Postbox) -> None:
    await box_can_mail(knocking)
    now = [1_000.0]
    opened(knocking, clock=lambda: now[0])
    async with stranger(knocking) as page:
        await page.post(SIGNUP, json=TIENDA)
        code = await code_mailed(knocking, postbox)
        now[0] += CODE_TTL_S
        late = await page.post(VERIFY, json={"email": TIENDA["email"], "code": code})
    assert (late.status_code, late.json()["detail"]) == (
        400,
        "that code has expired: ask for a new one",
    )


@postgres
async def test_a_code_for_an_address_nobody_signed_up_with_reads_as_a_wrong_one(
    knocking: Knocking,
) -> None:
    opened(knocking)
    async with stranger(knocking) as page:
        answer = await page.post(VERIFY, json={"email": "nadie@x.test", "code": "123456"})
    assert (answer.status_code, answer.json()["detail"]) == (400, "that code is not valid")


@postgres
async def test_a_resent_code_works_and_the_first_one_no_longer_does(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with stranger(knocking) as page:
        await page.post(SIGNUP, json=TIENDA)
        first = await code_mailed(knocking, mailing)
        resent = await page.post(RESEND, json={"email": TIENDA["email"]})
        second = await code_mailed(knocking, mailing)
        old = await page.post(VERIFY, json={"email": TIENDA["email"], "code": first})
        new = await page.post(VERIFY, json={"email": TIENDA["email"], "code": second})
    assert (resent.status_code, resent.json()) == (202, {})
    assert len(mailing.sent) == 2
    assert old.status_code == (201 if first == second else 400)
    assert new.status_code == (400 if first == second else 201)


@postgres
async def test_a_resend_for_an_unknown_address_answers_the_same_and_sends_nothing(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with stranger(knocking) as page:
        answer = await page.post(RESEND, json={"email": "nadie@x.test"})
    assert (answer.status_code, answer.json()) == (202, {})
    assert await delivered(knocking, mailing) == []


@postgres
async def test_a_slug_taken_while_the_code_travelled_is_refused_at_verify(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with stranger(knocking) as page:
        await page.post(SIGNUP, json=TIENDA)
        code = await code_mailed(knocking, mailing)
        await orgs.create(knocking.gateway.connections.pool, "tienda-sur", "Somebody faster")
        answer = await page.post(VERIFY, json={"email": TIENDA["email"], "code": code})
    assert answer.status_code == 409


# ── behind a shield ──


@postgres
async def test_with_a_shields_key_set_the_doors_take_that_key_and_nobody_else(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    opened(knocking, signup_key=THE_SHIELDS_KEY)
    async with stranger(knocking) as page:
        refused = [
            await page.post(door, json=body)
            for door, body in (
                (SIGNUP, TIENDA),
                (VERIFY, {"email": TIENDA["email"], "code": "123456"}),
                (RESEND, {"email": TIENDA["email"]}),
            )
        ]
        taken = await page.post(
            SIGNUP, json=TIENDA, headers={"Authorization": f"Bearer {THE_SHIELDS_KEY}"}
        )
    assert [item.status_code for item in refused] == [401] * 3
    assert (
        refused[0].json()["detail"] == "the sign-up doors take the key of the page in front of them"
    )
    assert taken.status_code == 202, taken.text
    await delivered(knocking, postbox)


@postgres
async def test_behind_the_key_the_throttle_counts_the_address_the_shield_says(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    opened(knocking, signup_key=THE_SHIELDS_KEY)
    shield = {"Authorization": f"Bearer {THE_SHIELDS_KEY}", "X-Pinecall-Client": "203.0.113.7"}
    async with stranger(knocking) as page:
        for n in range(TRIES):
            body = {**TIENDA, "org": f"org-{n}", "email": f"p{n}@x.test"}
            assert (await page.post(SIGNUP, json=body, headers=shield)).status_code == 202
        over = await page.post(SIGNUP, json={**TIENDA, "org": "one-more"}, headers=shield)
        elsewhere = await page.post(
            SIGNUP, json=TIENDA, headers={**shield, "X-Pinecall-Client": "198.51.100.9"}
        )
    assert over.status_code == 429
    assert elsewhere.status_code == 202, "another person, as the shield says"
    await delivered(knocking, postbox)


@postgres
async def test_without_a_key_the_shields_header_is_never_believed(
    knocking: Knocking, mailing: Postbox
) -> None:
    async with stranger(knocking) as page:
        for n in range(TRIES):
            body = {**TIENDA, "org": f"org-{n}", "email": f"p{n}@x.test"}
            faked = {"X-Pinecall-Client": f"203.0.113.{n}"}
            assert (await page.post(SIGNUP, json=body, headers=faked)).status_code == 202
        refused = await page.post(
            SIGNUP, json={**TIENDA, "org": "one-more"}, headers={"X-Pinecall-Client": "1.2.3.4"}
        )
    assert refused.status_code == 429
    await delivered(knocking, mailing)


def test_the_code_pattern_matches_six_digits_alone() -> None:
    assert A_CODE.search("Confirm\n\n042917\n\nThis code") is not None
    assert A_CODE.search("call 0429171234") is None
