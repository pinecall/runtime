"""Tests for the account doors: who a key is, sign-in, codes, the org switch, pairing, a reset."""

import httpx

from pinecall.domain.person import KEY_SCOPES, ROLE_SCOPES, Member, Role
from pinecall.tenancy import keys, orgs, people, sso, vault
from pinecall.tenancy.signin import NOBODY, TRIES
from pinecall.tenancy.sso import Client, OrgSso
from tests.conftest import Knocking, postgres
from tests.fakes.mail import Postbox
from tests.gateway.api.conftest import box_can_mail, delivered, text_of

WHAT_THEY_TYPE = "correct horse battery staple"
A_BETTER_ONE = "a much better horse battery staple"
BERNA = "berna@clinica.test"
LOGIN = "/v1/login"
PAIRINGS = "/v1/login/pairings"
A_LAPTOP = "berna-mbp"


async def a_person(
    knocking: Knocking, email: str = BERNA, *, role: Role = "developer", org: str | None = None
) -> Member:
    """A member of the org (or of another) who has chosen a password."""
    pool = knocking.gateway.connections.pool
    invitee = people.Invitee(email=email, name=email.split("@", maxsplit=1)[0], role=role)
    invited = await people.invite(pool, org or knocking.org.id, invitee, seats=None, vouched=True)
    if invited.token is None:
        return invited.member
    member = await people.accept(pool, invited.token, await people.hash_password(WHAT_THEY_TYPE, 8))
    assert member is not None
    return member


async def key_of(knocking: Knocking, member: Member) -> str:
    """A key of the member's own."""
    _, secret = await keys.person_key(knocking.gateway.connections.pool, member)
    return secret


def stranger(knocking: Knocking) -> httpx.AsyncClient:
    """A client with no key, as a sign-in page or a terminal is."""
    return httpx.AsyncClient(base_url=knocking.url)


# ── what a sign-in page reads first ──


@postgres
async def test_a_sign_in_page_is_told_the_gateway_before_it_holds_a_key(
    knocking: Knocking,
) -> None:
    async with stranger(knocking) as page:
        before = (await page.get("/.well-known/pinecall")).json()
        await box_can_mail(knocking)
        after = (await page.get("/.well-known/pinecall")).json()
    assert set(before) == {
        "version",
        "signup",
        "min_password",
        "mail",
        "brand",
        "google",
        "world",
        "elsewhere",
    }
    assert (before["world"], before["elsewhere"]) == (None, None)
    assert (before["signup"], before["min_password"], before["mail"], before["google"]) == (
        False,
        8,
        False,
        False,
    )
    assert before["brand"] == {"name": "Pinecall", "logo_url": None, "accent": "#5b3df5"}
    assert after["mail"] is True


# ── whoami ──


@postgres
async def test_whoami_names_the_org_the_key_and_what_it_opens_never_the_key(
    knocking: Knocking,
) -> None:
    secret = knocking.app["sandbox"]
    async with knocking.http(secret) as server:
        answer = await server.get("/v1/whoami")
    body = answer.json()
    assert answer.status_code == 200
    assert body == {
        "org": knocking.org.id,
        "slug": knocking.org.slug,
        "key_id": body["key_id"],
        "label": None,
        "env": "sandbox",
        "scopes": sorted(KEY_SCOPES),
        "subject": None,
        "name": None,
        "email": None,
        "operator": False,
        "visiting": False,
        "production": False,
    }
    assert secret not in answer.text
    assert not any("hash" in field or "fingerprint" in field for field in body)


@postgres
async def test_a_persons_whoami_says_who_the_world_asked_and_their_production_switch(
    knocking: Knocking,
) -> None:
    berna = await a_person(knocking, role="admin")
    async with knocking.http(await key_of(knocking, berna)) as console:
        sandbox = (await console.get("/v1/whoami")).json()
        production = (
            await console.get("/v1/whoami", headers={"pinecall-env": "production"})
        ).json()
    assert (sandbox["email"], sandbox["subject"], sandbox["env"]) == (BERNA, berna.id, "sandbox")
    assert (sandbox["production"], production["env"]) == (True, "production")


@postgres
async def test_a_terminal_with_no_key_is_told_which_door_this_is_and_a_stranger_nothing(
    knocking: Knocking,
) -> None:
    async with stranger(knocking) as terminal:
        none = await terminal.get("/v1/whoami")
    async with knocking.http("a-key-nobody-ever-issued") as invented:
        unknown = await invented.get("/v1/whoami")
    assert (none.status_code, none.json()["detail"]) == (401, "this door takes an API key")
    assert (unknown.status_code, unknown.json()["detail"]) == (401, "this door takes an API key")


# ── a password ──


@postgres
async def test_a_password_mints_a_key_for_that_person_and_device(knocking: Knocking) -> None:
    berna = await a_person(knocking, role="supervisor")
    body = {"org": knocking.org.slug, "email": BERNA, "password": WHAT_THEY_TYPE, "device": "phone"}
    async with stranger(knocking) as page:
        signed = await page.post(LOGIN, json=body)
    assert signed.status_code == 200, signed.text
    answer = signed.json()
    assert set(answer) == {"key", "key_id", "org", "label", "env", "scopes", "subject", "name"}
    assert (answer["label"], answer["env"], answer["subject"]) == ("phone", "production", berna.id)
    assert answer["scopes"] == sorted(ROLE_SCOPES["supervisor"])
    assert answer["key"].startswith("pc_live_")


@postgres
async def test_a_wrong_password_a_wrong_email_and_a_wrong_org_are_one_sentence(
    knocking: Knocking,
) -> None:
    await a_person(knocking)
    wrong = [
        {"org": knocking.org.slug, "email": BERNA, "password": "not the password at all"},
        {"org": knocking.org.slug, "email": "nobody@clinica.test", "password": WHAT_THEY_TYPE},
        {"org": "tienda", "email": BERNA, "password": WHAT_THEY_TYPE},
    ]
    async with stranger(knocking) as page:
        refused = [await page.post(LOGIN, json=body) for body in wrong]
    assert [(item.status_code, item.json()["detail"]) for item in refused] == [(401, NOBODY)] * 3


@postgres
async def test_a_member_still_invited_is_told_nothing_a_stranger_is_not(knocking: Knocking) -> None:
    await people.invite(
        knocking.gateway.connections.pool,
        knocking.org.id,
        people.Invitee(BERNA, "Berna", "developer"),
        seats=None,
    )
    async with stranger(knocking) as page:
        refused = await page.post(LOGIN, json={"email": BERNA, "password": WHAT_THEY_TYPE})
    assert (refused.status_code, refused.json()["detail"]) == (401, NOBODY)


@postgres
async def test_the_sixth_try_in_a_minute_is_429_whatever_the_password(knocking: Knocking) -> None:
    await a_person(knocking)
    wrong = {"email": BERNA, "password": "not the password at all"}
    async with stranger(knocking) as page:
        misses = [(await page.post(LOGIN, json=wrong)).status_code for _ in range(TRIES)]
        right = await page.post(LOGIN, json={**wrong, "password": WHAT_THEY_TYPE})
    assert misses == [401] * TRIES
    assert right.status_code == 429
    assert BERNA in right.json()["detail"]


@postgres
async def test_an_org_that_signs_in_with_its_provider_refuses_the_password_after_it_matched(
    knocking: Knocking,
) -> None:
    await a_person(knocking)
    connections = knocking.gateway.connections
    client = Client("https://idp.test", "the-client", "shh")
    await sso.put_sso(
        connections.pool,
        connections.vault,
        OrgSso(knocking.org.id, client, ("clinica.test",), required=True),
    )
    async with stranger(knocking) as page:
        refused = await page.post(LOGIN, json={"email": BERNA, "password": WHAT_THEY_TYPE})
    assert refused.status_code == 403
    assert "/v1/login/sso?org=clinica-norte" in refused.json()["detail"]


@postgres
async def test_a_login_says_one_thing_or_the_other(knocking: Knocking) -> None:
    async with stranger(knocking) as page:
        both = await page.post(LOGIN, json={"code": "lc_x", "email": "a@b.c"})
        neither = await page.post(LOGIN, json={"org": knocking.org.slug})
        no_code_door = await page.post("/v1/login/codes")
    assert (both.status_code, neither.status_code) == (400, 400)
    assert both.json()["detail"] == neither.json()["detail"]
    assert no_code_door.status_code == 401, "minting a code takes a key"


# ── the orgs a person opens ──


@postgres
async def test_a_password_lists_the_orgs_it_opens_minting_nothing(knocking: Knocking) -> None:
    other = await orgs.create(knocking.gateway.connections.pool, "tienda-sur", "Tienda Sur")
    await a_person(knocking)
    await a_person(knocking, org=other.id, role="qa")
    async with stranger(knocking) as page:
        listed = await page.post(
            "/v1/login/orgs", json={"email": BERNA, "password": WHAT_THEY_TYPE}
        )
        wrong = await page.post("/v1/login/orgs", json={"email": BERNA, "password": "no"})
    assert listed.json() == {
        "orgs": [
            {
                "org": knocking.org.id,
                "slug": "clinica-norte",
                "name": "Clinica Norte",
                "role": "developer",
            },
            {"org": other.id, "slug": "tienda-sur", "name": "Tienda Sur", "role": "qa"},
        ]
    }
    assert (wrong.status_code, wrong.json()["detail"]) == (401, NOBODY)
    listing = await keys.listed(knocking.gateway.connections.pool, other.id)
    assert listing == [], "a listing mints no key"


@postgres
async def test_a_persons_orgs_mark_the_one_the_key_opens_and_an_operator_sees_every_org(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    other = await orgs.create(pool, "tienda-sur", "Tienda Sur")
    berna = await a_person(knocking)
    async with knocking.http(await key_of(knocking, berna)) as console:
        mine = (await console.get("/v1/login/orgs")).json()["orgs"]
        operator = await people.make_operator(pool, knocking.org.id, berna.id, on=True)
        assert operator is not None
        # As the operator's door does: the gateway forgets what it remembered of the person.
        knocking.gateway.keys.forget(subject=berna.id)
        every = (await console.get("/v1/login/orgs")).json()["orgs"]
    async with knocking.http(knocking.app["sandbox"]) as server:
        refused = await server.get("/v1/login/orgs")
    assert [(row["org"], row["here"], row["member"]) for row in mine] == [
        (knocking.org.id, True, True)
    ]
    rows = [(row["org"], row["role"], row["member"]) for row in every]
    assert rows[0] == (knocking.org.id, "developer", True)
    assert (other.id, "operator", False) in rows
    assert all(role == "operator" for _, role, member in rows[1:] if not member)
    assert refused.status_code == 403


@postgres
async def test_the_switch_mints_the_same_persons_key_in_another_org_of_theirs_or_a_visit(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    other = await orgs.create(pool, "tienda-sur", "Tienda Sur")
    stranger_org = await orgs.create(pool, "acme", "Acme")
    berna = await a_person(knocking)
    theirs = await a_person(knocking, org=other.id, role="qa")
    async with knocking.http(await key_of(knocking, berna)) as console:
        switched = (await console.post("/v1/login/org", json={"org": "tienda-sur"})).json()
        refused = await console.post("/v1/login/org", json={"org": "acme"})
        await people.make_operator(pool, knocking.org.id, berna.id, on=True)
        knocking.gateway.keys.forget(subject=berna.id)
        visit = (await console.post("/v1/login/org", json={"org": "acme"})).json()
    async with knocking.http(knocking.app["sandbox"]) as server:
        server_refused = await server.post("/v1/login/org", json={"org": "tienda-sur"})
    assert (switched["org"], switched["subject"]) == (other.id, theirs.id)
    assert switched["scopes"] == sorted(ROLE_SCOPES["qa"])
    assert refused.status_code == 403
    async with knocking.http(visit["key"]) as visiting:
        who = (await visiting.get("/v1/whoami")).json()
    assert (who["org"], who["visiting"], who["operator"]) == (stranger_org.id, True, True)
    assert server_refused.status_code == 403


# ── a code for a browser ──


@postgres
async def test_a_code_minted_by_a_key_holder_logs_a_browser_in_with_a_key_of_its_own(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as server:
        minted = (await server.post("/v1/login/codes")).json()
    async with stranger(knocking) as browser:
        signed = await browser.post(LOGIN, json={"code": minted["code"]})
        spent = await browser.post(LOGIN, json={"code": minted["code"]})
    assert minted["code"].startswith("lc_")
    answer = signed.json()
    assert answer["key"] != knocking.app["sandbox"]
    assert (answer["org"], answer["env"], answer["label"]) == (
        knocking.org.id,
        "sandbox",
        "console",
    )
    assert answer["scopes"] == sorted(KEY_SCOPES)
    assert spent.status_code == 404


@postgres
async def test_a_persons_code_gives_the_browser_that_person_labelled_as_the_device(
    knocking: Knocking,
) -> None:
    berna = await a_person(knocking)
    async with knocking.http(await key_of(knocking, berna)) as console:
        minted = (await console.post("/v1/login/codes")).json()
    async with stranger(knocking) as browser:
        signed = await browser.post(LOGIN, json={"code": minted["code"], "device": "chrome"})
    assert (signed.json()["subject"], signed.json()["label"]) == (berna.id, "chrome")


# ── a forgotten password ──


@postgres
async def test_a_forgotten_password_mails_the_link_that_opens_the_invitations_card(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    await a_person(knocking)
    async with stranger(knocking) as page:
        known = await page.post("/v1/login/reset", json={"email": BERNA})
        unknown = await page.post("/v1/login/reset", json={"email": "nobody@nowhere.test"})
        assert (known.status_code, known.json()) == (202, {})
        assert (unknown.status_code, unknown.text) == (known.status_code, known.text)
        assert await delivered(knocking, postbox) == [BERNA]
        text = text_of(postbox.sent[-1])
        assert "https://box.test/invitations/" in text
        token = text.split("/invitations/")[1].split()[0]
        accepted = await page.post(f"/v1/invitations/{token}", json={"password": A_BETTER_ONE})
        old = await page.post(LOGIN, json={"email": BERNA, "password": WHAT_THEY_TYPE})
        new = await page.post(LOGIN, json={"email": BERNA, "password": A_BETTER_ONE})
    assert accepted.status_code == 200
    assert (old.status_code, new.status_code) == (401, 200)


@postgres
async def test_a_member_still_invited_is_sent_no_reset_because_the_invitation_is_the_link(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    await people.invite(
        knocking.gateway.connections.pool,
        knocking.org.id,
        people.Invitee(BERNA, "Berna", "developer"),
        seats=None,
    )
    async with stranger(knocking) as page:
        answers = await page.post("/v1/login/reset", json={"email": BERNA})
    assert answers.status_code == 202
    assert await delivered(knocking, postbox) == []


@postgres
async def test_the_sixth_reset_in_a_minute_is_the_logins_own_refusal(knocking: Knocking) -> None:
    async with stranger(knocking) as page:
        answers = [
            (await page.post("/v1/login/reset", json={"email": "a@b.test"})).status_code
            for _ in range(TRIES)
        ]
        stopped = await page.post("/v1/login/reset", json={"email": "a@b.test"})
    assert answers == [202] * TRIES
    assert stopped.status_code == 429
    assert "try again in a minute" in stopped.json()["detail"]


# ── a terminal paired with a browser ──


@postgres
async def test_the_terminal_waits_until_the_browser_answers_and_then_gets_a_key_of_its_own(
    knocking: Knocking,
) -> None:
    berna = await a_person(knocking)
    browsers = await key_of(knocking, berna)
    async with stranger(knocking) as terminal, knocking.http(browsers) as browser:
        code = (await terminal.post(PAIRINGS, json={"device": A_LAPTOP})).json()["code"]
        waiting = await terminal.get(f"{PAIRINGS}/{code}/key")
        approved = await browser.post(f"{PAIRINGS}/{code}")
        collected = await terminal.get(f"{PAIRINGS}/{code}/key")
        again = await terminal.get(f"{PAIRINGS}/{code}/key")
    assert (waiting.status_code, waiting.json()) == (202, {})
    assert approved.json() == {"device": A_LAPTOP, "org": knocking.org.id}
    key = collected.json()["key"]
    assert key != browsers
    bearer = await keys.verify(knocking.gateway.connections.pool, key)
    assert bearer is not None
    assert (bearer.key.subject, bearer.key.label) == (berna.id, A_LAPTOP)
    assert again.status_code == 404, "collected once, and gone after"


@postgres
async def test_the_card_says_which_terminal_and_reading_it_spends_nothing(
    knocking: Knocking,
) -> None:
    berna = await a_person(knocking)
    async with stranger(knocking) as terminal, knocking.http(await key_of(knocking, berna)) as page:
        code = (await terminal.post(PAIRINGS, json={"device": A_LAPTOP})).json()["code"]
        card = await page.get(f"{PAIRINGS}/{code}")
        still = await terminal.get(f"{PAIRINGS}/{code}/key")
    assert card.json()["device"] == A_LAPTOP
    assert card.json()["answered"] is False
    assert "key" not in card.json()
    assert still.status_code == 202


@postgres
async def test_a_second_approval_is_refused_and_an_orgs_own_key_signs_nobody_in(
    knocking: Knocking,
) -> None:
    berna = await a_person(knocking)
    async with stranger(knocking) as terminal, knocking.http(await key_of(knocking, berna)) as page:
        code = (await terminal.post(PAIRINGS, json={})).json()["code"]
        await page.post(f"{PAIRINGS}/{code}")
        again = await page.post(f"{PAIRINGS}/{code}")
        second = (await terminal.post(PAIRINGS, json={})).json()["code"]
    async with knocking.http(knocking.app["sandbox"]) as server:
        nobody = await server.post(f"{PAIRINGS}/{second}")
    assert again.status_code == 409
    assert nobody.status_code == 403
    assert "names nobody" in nobody.json()["detail"]


@postgres
async def test_a_word_nobody_printed_is_the_same_answer_as_one_that_died(
    knocking: Knocking,
) -> None:
    berna = await a_person(knocking)
    async with stranger(knocking) as terminal, knocking.http(await key_of(knocking, berna)) as page:
        collected = await terminal.get(f"{PAIRINGS}/cli_never_minted/key")
        card = await page.get(f"{PAIRINGS}/cli_never_minted")
        approved = await page.post(f"{PAIRINGS}/cli_never_minted")
    assert (collected.status_code, card.status_code, approved.status_code) == (404, 404, 404)
    assert collected.json()["detail"] == card.json()["detail"] == approved.json()["detail"]


# ── an invitation accepted ──


@postgres
async def test_accepting_an_invitation_makes_the_member_active_with_their_first_key(
    knocking: Knocking,
) -> None:
    invited = await people.invite(
        knocking.gateway.connections.pool,
        knocking.org.id,
        people.Invitee(BERNA, "Berna", "developer"),
        seats=None,
    )
    async with stranger(knocking) as page:
        accepted = await page.post(
            f"/v1/invitations/{invited.token}",
            json={"password": WHAT_THEY_TYPE, "device": "laptop"},
        )
        spent = await page.post(
            f"/v1/invitations/{invited.token}", json={"password": WHAT_THEY_TYPE}
        )
        invented = await page.post("/v1/invitations/inv_x", json={"password": WHAT_THEY_TYPE})
        short = await page.post("/v1/invitations/inv_x", json={"password": "a" * 7})
    body = accepted.json()
    assert (body["member"]["status"], body["label"], body["subject"]) == (
        "active",
        "laptop",
        invited.member.id,
    )
    assert body["scopes"] == sorted(ROLE_SCOPES["developer"])
    assert (spent.status_code, invented.status_code) == (404, 404)
    assert spent.json()["detail"] == invented.json()["detail"]
    assert short.status_code == 400
    assert "at least 8" in short.json()["detail"]


# ── a vendor key the org brought ──


@postgres
async def test_no_account_door_answers_with_a_vendor_key_the_org_brought(
    knocking: Knocking,
) -> None:
    connections = knocking.gateway.connections
    canary = "the-orgs-own-vendor-key-canary"
    await vault.put_credentials(
        connections.pool, connections.vault, knocking.org.id, "acme", {"api_key": canary}
    )
    berna = await a_person(knocking, role="admin")
    doors = (
        "/.well-known/pinecall",
        "/v1/whoami",
        "/v1/login/orgs",
        "/v1/keys",
        "/v1/members",
        "/v1/org/sso",
        "/v1/org/mail",
    )
    async with knocking.http(await key_of(knocking, berna)) as console:
        answers = {door: await console.get(door) for door in doors}
    assert all(answer.status_code == 200 for answer in answers.values())
    assert [door for door, answer in answers.items() if canary in answer.text] == []
