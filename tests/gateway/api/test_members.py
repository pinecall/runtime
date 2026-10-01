"""Tests for the members doors: listed, invited, changed, reset, removed, and their letters."""

from typing import Any

import httpx

from pinecall.domain.org import Quotas
from pinecall.domain.person import ROLE_SCOPES, Member, Role
from pinecall.tenancy import admission, keys, orgs, people
from pinecall.tenancy.keys import NOT_YOURS_TO_GRANT
from tests.conftest import BOX_DOMAIN, Knocking, postgres
from tests.fakes.mail import Postbox
from tests.gateway.api.conftest import box_can_mail, delivered, text_of

MEMBERS = "/v1/members"
WHAT_THEY_TYPE = "correct horse battery staple"
BERNAS = "berna@clinica.test"
BERNA = {"email": BERNAS, "name": "Berna", "role": "developer", "agents": ["a"]}
MANAGERS_OPEN = " · ".join(sorted(ROLE_SCOPES["manager"]))


async def seated(
    knocking: Knocking, email: str, role: Role = "developer", *, production: bool = False
) -> tuple[Member, str]:
    """A member who chose a password, and a key of theirs."""
    pool = knocking.gateway.connections.pool
    invitee = people.Invitee(email, email.split("@", maxsplit=1)[0].title(), role)
    invited = await people.invite(pool, knocking.org.id, invitee, seats=None)
    assert invited.token is not None
    member = await people.accept(pool, invited.token, await people.hash_password(WHAT_THEY_TYPE, 8))
    assert member is not None
    if production:
        member = await people.update(
            pool, knocking.org.id, member.id, people.Change(production=True)
        )
    _, secret = await keys.person_key(pool, member)
    return member, secret


async def invited(console: httpx.AsyncClient, **changed: object) -> dict[str, Any]:
    """An invitation through the door, answered 201."""
    answer = await console.post(MEMBERS, json={**BERNA, **changed})
    assert answer.status_code == 201, answer.text
    return answer.json()


# ── listing and inviting ──


@postgres
async def test_an_invite_answers_the_row_and_the_token_once_and_the_listing_never_shows_it(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        answer = await invited(console)
        listing = await console.get(MEMBERS)
    assert answer["token"].startswith("inv_")
    assert answer["link"] == f"https://{BOX_DOMAIN}/invitations/{answer['token']}"
    assert answer["mailed"] is False, "nothing on this box can post a letter"
    assert (answer["member"]["status"], answer["member"]["agents"]) == ("invited", ["a"])
    assert answer["member"]["scopes"] == sorted(ROLE_SCOPES["developer"])
    assert [row["email"] for row in listing.json()["members"]] == [BERNAS]
    assert answer["token"] not in listing.text


@postgres
async def test_an_invitation_is_mailed_with_the_very_token_the_answer_carries(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    _, anas = await seated(knocking, "ana@clinica.test", "admin")
    async with knocking.http(anas) as console:
        answer = await invited(console)
    assert answer["mailed"] is True
    assert await delivered(knocking, postbox) == [BERNAS]
    text = text_of(postbox.sent[0])
    assert f"https://box.test/invitations/{answer['token']}" in text
    assert "Ana" in text
    assert "Clinica Norte" in str(postbox.sent[0]["Subject"])


@postgres
async def test_somebody_in_another_org_is_mailed_the_link_and_the_admin_is_never_handed_it(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    other = await orgs.create(knocking.gateway.connections.pool, "tienda-sur", "Tienda Sur")
    await people.invite(
        knocking.gateway.connections.pool,
        other.id,
        people.Invitee(BERNAS, "Berna", "qa"),
        seats=None,
    )
    async with knocking.http(knocking.app["production"]) as console:
        answer = await invited(console)
    assert (answer["token"], answer["mailed"]) == (None, True)
    assert await delivered(knocking, postbox) == [BERNAS]


@postgres
async def test_a_person_proven_elsewhere_with_a_password_is_seated_at_once(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    other = await orgs.create(pool, "tienda-sur", "Tienda Sur")
    elsewhere = await people.invite(
        pool, other.id, people.Invitee(BERNAS, "Berna", "qa"), seats=None, vouched=True
    )
    assert elsewhere.token is not None
    await people.accept(pool, elsewhere.token, await people.hash_password(WHAT_THEY_TYPE, 8))
    async with knocking.http(knocking.app["production"]) as console:
        answer = await invited(console)
    assert (answer["token"], answer["member"]["status"]) == (None, "active")


@postgres
async def test_the_refusals_of_the_invite_door_are_sentences(knocking: Knocking) -> None:
    await seated(knocking, BERNAS)
    async with knocking.http(knocking.app["production"]) as console:
        again = await console.post(MEMBERS, json=BERNA)
        role = await console.post(MEMBERS, json={**BERNA, "email": "a@b.test", "role": "root"})
        email = await console.post(MEMBERS, json={**BERNA, "email": "ana"})
    async with httpx.AsyncClient(base_url=knocking.url) as nobody:
        keyless = await nobody.get(MEMBERS)
    assert again.status_code == 409
    assert BERNAS in again.json()["detail"]
    assert role.status_code == 400
    assert "a role is one of" in role.json()["detail"]
    assert email.status_code == 400
    assert "one @" in email.json()["detail"]
    assert keyless.status_code == 401


@postgres
async def test_the_seats_are_the_orgs_limit_and_removing_one_frees_it(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    await admission.set_quotas(pool, knocking.org.id, "production", Quotas(seats=1))
    async with knocking.http(knocking.app["production"]) as console:
        first = await invited(console)
        full = await console.post(MEMBERS, json={**BERNA, "email": "b@clinica.test"})
        removed = await console.delete(f"{MEMBERS}/{first['member']['id']}")
        again = await invited(console)
    assert full.status_code == 429
    assert removed.status_code == 204
    assert again["member"]["id"] != first["member"]["id"], "a new row: the old one is gone"


# ── what a key may grant ──


@postgres
async def test_a_manager_invites_a_qa_and_is_refused_an_admin_and_a_developer(
    knocking: Knocking,
) -> None:
    _, martas = await seated(knocking, "marta@clinica.test", "manager", production=True)
    async with knocking.http(martas) as marta:
        qa = await marta.post(MEMBERS, json={**BERNA, "role": "qa"})
        above = [
            await marta.post(MEMBERS, json={**BERNA, "email": f"{role}@x.test", "role": role})
            for role in ("admin", "developer")
        ]
        listed = (await marta.get(MEMBERS)).json()["members"]
    assert qa.status_code == 201
    assert [item.status_code for item in above] == [403, 403]
    assert above[0].json()["detail"] == NOT_YOURS_TO_GRANT.format(role="admin", opens=MANAGERS_OPEN)
    assert sorted(row["role"] for row in listed) == ["manager", "qa"], "nothing half-made"


@postgres
async def test_a_manager_may_not_raise_a_colleague_to_admin_and_may_within_reach(
    knocking: Knocking,
) -> None:
    _, martas = await seated(knocking, "marta@clinica.test", "manager", production=True)
    diego, _ = await seated(knocking, "diego@clinica.test", "qa")
    async with knocking.http(martas) as marta:
        raised = await marta.patch(f"{MEMBERS}/{diego.id}", json={"role": "admin"})
        within = await marta.patch(f"{MEMBERS}/{diego.id}", json={"role": "supervisor"})
    assert raised.status_code == 403
    assert within.status_code == 200
    assert (within.json()["role"], within.json()["production"]) == ("supervisor", False)


@postgres
async def test_nobody_changes_their_own_role_or_switch(knocking: Knocking) -> None:
    marta, martas = await seated(knocking, "marta@clinica.test", "manager", production=True)
    async with knocking.http(martas) as own:
        refused = [
            await own.patch(f"{MEMBERS}/{marta.id}", json=body)
            for body in ({"role": "admin"}, {"production": True}, {"role": "manager"})
        ]
        narrowed = await own.patch(f"{MEMBERS}/{marta.id}", json={"agents": ["clinica-norte"]})
    assert [item.status_code for item in refused] == [409] * 3
    assert narrowed.status_code == 200


@postgres
async def test_the_orgs_own_key_seats_an_admin_and_an_admin_keeps_production(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        admin = await invited(console, role="admin", production=True)
        taken = await console.patch(
            f"{MEMBERS}/{admin['member']['id']}", json={"production": False}
        )
    assert admin["member"]["production"] is True
    assert taken.status_code == 409
    assert "always opens production" in taken.json()["detail"]


# ── changing and removing ──


@postgres
async def test_disabling_a_member_revokes_their_keys_and_refuses_their_login(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    berna, bernas = await seated(knocking, BERNAS)
    async with knocking.http(knocking.app["production"]) as console:
        disabled = await console.patch(f"{MEMBERS}/{berna.id}", json={"status": "disabled"})
        assert await keys.verify(pool, bernas) is None, "the person's key is stopped"
        assert await keys.verify(pool, knocking.app["production"]) is not None
        async with httpx.AsyncClient(base_url=knocking.url) as page:
            login = await page.post("/v1/login", json={"email": BERNAS, "password": WHAT_THEY_TYPE})
        back = await console.patch(f"{MEMBERS}/{berna.id}", json={"status": "active"})
    assert disabled.json()["status"] == "disabled"
    assert login.status_code == 403
    assert "disabled" in login.json()["detail"]
    assert back.json()["status"] == "active"


@postgres
async def test_a_role_change_presets_the_next_key_and_an_invited_member_is_not_activated_by_hand(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        member = (await invited(console))["member"]
        changed = await console.patch(
            f"{MEMBERS}/{member['id']}", json={"role": "qa", "agents": ["tienda-sur"]}
        )
        by_hand = await console.patch(f"{MEMBERS}/{member['id']}", json={"status": "active"})
        nobody = await console.patch(f"{MEMBERS}/m_nobody", json={"role": "qa"})
        unknown = await console.patch(f"{MEMBERS}/{member['id']}", json={"status": "gone"})
    assert (changed.json()["role"], changed.json()["agents"]) == ("qa", ["tienda-sur"])
    assert changed.json()["scopes"] == sorted(ROLE_SCOPES["qa"])
    assert by_hand.status_code == 400
    assert "accepting it" in by_hand.json()["detail"]
    assert (nobody.status_code, unknown.status_code) == (404, 400)


@postgres
async def test_removing_a_member_stops_their_keys_forgets_the_row_and_refuses_their_login(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    ana, anas = await seated(knocking, "ana@clinica.test")
    async with knocking.http(knocking.app["production"]) as console:
        gone = await console.delete(f"{MEMBERS}/{ana.id}")
        listing = (await console.get(MEMBERS)).json()["members"]
    async with knocking.http(anas) as theirs:
        whoami = await theirs.get("/v1/whoami")
    assert (gone.status_code, gone.content) == (204, b"")
    assert whoami.status_code == 401
    assert listing == []
    kept = [row for row in await keys.listed(pool, knocking.org.id) if row.key.subject == ana.id]
    assert kept
    assert all(row.revoked_at is not None for row in kept), "the rows stay, revoked"


@postgres
async def test_the_link_of_somebody_removed_while_still_invited_opens_nothing(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        berna = await invited(console)
        await console.delete(f"{MEMBERS}/{berna['member']['id']}")
    async with httpx.AsyncClient(base_url=knocking.url) as page:
        spent = await page.post(
            f"/v1/invitations/{berna['token']}", json={"password": WHAT_THEY_TYPE}
        )
    assert spent.status_code == 404


@postgres
async def test_nobody_removes_or_disables_themselves_and_the_last_active_admin_stays(
    knocking: Knocking,
) -> None:
    ana, anas = await seated(knocking, "ana@clinica.test", "admin")
    async with knocking.http(anas) as hers:
        myself = await hers.delete(f"{MEMBERS}/{ana.id}")
        disabled = await hers.patch(f"{MEMBERS}/{ana.id}", json={"status": "disabled"})
        still = await hers.get(MEMBERS)
    async with knocking.http(knocking.app["production"]) as console:
        await invited(console, role="admin")
        last = await console.delete(f"{MEMBERS}/{ana.id}")
    carla, _ = await seated(knocking, "carla@clinica.test", "admin")
    async with knocking.http(anas) as hers:
        other = await hers.delete(f"{MEMBERS}/{carla.id}")
    assert (myself.status_code, disabled.status_code) == (409, 409)
    assert still.status_code == 200, "her key still opens the door: nothing was revoked"
    assert last.status_code == 409
    assert other.status_code == 204


@postgres
async def test_a_key_without_team_removes_nobody_and_an_id_not_the_orgs_is_404(
    knocking: Knocking,
) -> None:
    _, qas = await seated(knocking, "qa@clinica.test", "qa")
    berna, _ = await seated(knocking, BERNAS)
    async with knocking.http(qas) as qa:
        refused = await qa.delete(f"{MEMBERS}/{berna.id}")
    async with knocking.http(knocking.app["production"]) as console:
        nobody = await console.delete(f"{MEMBERS}/m_nobody")
    assert refused.status_code == 403
    assert nobody.status_code == 404


# ── an admin's reset ──


@postgres
async def test_an_admins_reset_is_mailed_to_the_member_and_says_who_reset_it(
    knocking: Knocking, postbox: Postbox
) -> None:
    await box_can_mail(knocking)
    berna, _ = await seated(knocking, BERNAS)
    _, anas = await seated(knocking, "ana@clinica.test", "admin")
    async with knocking.http(anas) as console:
        answer = await console.post(f"{MEMBERS}/{berna.id}/reset")
    assert answer.status_code == 201
    assert answer.json()["mailed"] is True
    assert await delivered(knocking, postbox) == [BERNAS]
    text = text_of(postbox.sent[-1])
    assert f"https://box.test/invitations/{answer.json()['token']}" in text
    assert "Ana" in text


@postgres
async def test_a_reset_with_nothing_to_mail_hands_the_link_and_an_invited_member_is_409(
    knocking: Knocking,
) -> None:
    berna, _ = await seated(knocking, BERNAS)
    async with knocking.http(knocking.app["production"]) as console:
        reset = await console.post(f"{MEMBERS}/{berna.id}/reset")
        pending = (await invited(console, email="new@clinica.test"))["member"]
        refused = await console.post(f"{MEMBERS}/{pending['id']}/reset")
        nobody = await console.post(f"{MEMBERS}/m_nobody/reset")
    assert (reset.json()["mailed"], reset.json()["token"][:4]) == (False, "inv_")
    assert refused.status_code == 409
    assert nobody.status_code == 404


@postgres
async def test_a_member_disabled_at_the_door_is_refused_on_the_next_request_not_seconds_later(
    knocking: Knocking,
) -> None:
    _, admins = await seated(knocking, "ana@clinica.test", "admin")
    bo, bos = await seated(knocking, "bo@clinica.test", "admin")
    async with knocking.http(bos) as bo_console:
        before = await bo_console.get("/v1/members")
        async with knocking.http(admins) as admin:
            changed = await admin.patch(f"/v1/members/{bo.id}", json={"status": "disabled"})
        after = await bo_console.get("/v1/members")
    assert (before.status_code, changed.status_code) == (200, 200)
    assert after.status_code == 401, "the key the gateway remembered is forgotten at once"
