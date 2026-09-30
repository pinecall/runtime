"""Tests for the org's own settings: judging, its identity provider, its mailbox."""

import json
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest

from pinecall.domain.scope import Scope
from pinecall.gateway.app import app
from pinecall.tenancy import mail, reads, sso
from pinecall.tenancy.reads import Read
from tests.conftest import Knocking, postgres
from tests.fakes.idp import IdentityProvider
from tests.fakes.mail import Postbox
from tests.gateway.api.conftest import delivered

THE_SSO = "/v1/org/sso"
THE_MAIL = "/v1/org/mail"
THE_CLIENTS_SHH = "shh-the-clients-secret"
THE_MAILBOXS_SHH = "an-ses-smtp-password"
THE_ORGS_SENDER = "Clinica <no-reply@clinica.test>"
WIRED = {
    "issuer": "https://idp.test",
    "client_id": "the-client",
    "client_secret": THE_CLIENTS_SHH,
    "domains": ["Clinica.test", "@tienda.test"],
    "role": "developer",
}
A_MAILBOX = {
    "host": "smtp.clinica.test",
    "port": 587,
    "security": "starttls",
    "username": "AKIAEXAMPLE",
    "password": THE_MAILBOXS_SHH,
    "from": THE_ORGS_SENDER,
}


@pytest.fixture
async def idp(knocking: Knocking) -> AsyncIterator[IdentityProvider]:
    """The gateway's HTTP reaching an identity provider at https://idp.test."""
    provider = IdentityProvider()
    async with httpx.AsyncClient(transport=provider.transport()) as http:
        connections = replace(knocking.gateway.connections, http=http)
        app.state.gateway = replace(knocking.gateway, connections=connections)
        yield provider


@postgres
async def test_judging_is_turned_off_for_the_org(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        before = (await tenant.get("/v1/org/judging")).json()
        after = (await tenant.put("/v1/org/judging", json={"on": False})).json()
    assert (before["on"], after["on"]) == (True, False)


# ── the identity provider ──


@postgres
async def test_an_org_with_no_provider_says_so_and_names_the_uri_to_register(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        answer = await console.get(THE_SSO)
    assert answer.json() == {
        "configured": False,
        "issuer": None,
        "client_id": None,
        "domains": [],
        "role": None,
        "required": False,
        "redirect_uri": "https://box.test/v1/login/sso/callback",
    }


@postgres
@pytest.mark.usefixtures("idp")
async def test_a_provider_is_kept_with_its_domains_folded_and_its_secret_never_answered(
    knocking: Knocking,
) -> None:
    connections = knocking.gateway.connections
    async with knocking.http(knocking.app["production"]) as console:
        answer = await console.put(THE_SSO, json=WIRED)
        read = await console.get(THE_SSO)
    assert answer.status_code == 200, answer.text
    assert answer.json()["domains"] == ["clinica.test", "tienda.test"]
    assert (answer.json()["configured"], answer.json()["required"]) == (True, False)
    assert THE_CLIENTS_SHH not in answer.text
    assert THE_CLIENTS_SHH not in read.text
    kept = await sso.sso_of(connections.pool, connections.vault, knocking.org.id)
    assert kept is not None
    assert kept.client.client_secret == THE_CLIENTS_SHH


@postgres
@pytest.mark.usefixtures("idp")
async def test_an_issuer_nobody_answers_at_or_not_https_is_refused_and_nothing_is_kept(
    knocking: Knocking,
) -> None:
    connections = knocking.gateway.connections
    async with knocking.http(knocking.app["production"]) as console:
        nobody = await console.put(THE_SSO, json={**WIRED, "issuer": "https://nobody.test"})
        plain = await console.put(THE_SSO, json={**WIRED, "issuer": "http://idp.test"})
        role = await console.put(THE_SSO, json={**WIRED, "role": "boss"})
    assert nobody.status_code == 400
    assert "nothing was kept" in nobody.json()["detail"]
    assert plain.status_code == 400
    assert "https" in plain.json()["detail"]
    assert role.status_code == 400
    assert "'boss'" in role.json()["detail"]
    assert await sso.sso_of(connections.pool, connections.vault, knocking.org.id) is None


@postgres
@pytest.mark.usefixtures("idp")
async def test_a_provider_taken_away_twice_is_a_404_the_second_time(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        await console.put(THE_SSO, json=WIRED)
        first = await console.delete(THE_SSO)
        second = await console.delete(THE_SSO)
    assert first.status_code == 204
    assert second.status_code == 404


# ── the mailbox ──


@postgres
async def test_a_mailbox_password_goes_in_and_never_comes_out(knocking: Knocking) -> None:
    connections = knocking.gateway.connections
    async with knocking.http(knocking.app["production"]) as console:
        put = await console.put(THE_MAIL, json=A_MAILBOX)
        read = await console.get(THE_MAIL)
    assert put.status_code == 200
    assert THE_MAILBOXS_SHH not in put.text
    assert THE_MAILBOXS_SHH not in read.text
    assert (read.json()["configured"], read.json()["from"], read.json()["username"]) == (
        True,
        THE_ORGS_SENDER,
        "AKIAEXAMPLE",
    )
    kept = await mail.mail_of(connections.pool, connections.vault, knocking.org.id)
    assert kept is not None
    assert kept.mailbox.password == THE_MAILBOXS_SHH


@postgres
async def test_an_org_with_no_mailbox_answers_one_shape_with_every_field_empty(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        answer = await console.get(THE_MAIL)
        gone = await console.delete(THE_MAIL)
    assert answer.json() == {
        "configured": False,
        "host": None,
        "port": None,
        "security": None,
        "username": None,
        "from": None,
        "verified_at": None,
        "last_error": None,
    }
    assert gone.status_code == 404


@postgres
@pytest.mark.parametrize(
    "wrong", [{"security": "carrier-pigeon"}, {"port": 0}, {"from": "Pinecall"}, {"host": "a b"}]
)
async def test_a_mailbox_that_is_not_one_is_refused_and_nothing_is_kept(
    knocking: Knocking, wrong: dict[str, object]
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        answer = await console.put(THE_MAIL, json={**A_MAILBOX, **wrong})
        read = await console.get(THE_MAIL)
    assert answer.status_code == 400
    assert read.json()["configured"] is False


@postgres
async def test_a_test_letter_says_what_the_server_said_rather_than_failing_the_door(
    knocking: Knocking, postbox: Postbox
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        nothing = await console.post(f"{THE_MAIL}/test", json={"to": "ana@clinica.test"})
        await console.put(THE_MAIL, json=A_MAILBOX)
        went = await console.post(f"{THE_MAIL}/test", json={"to": "ana@clinica.test"})
        status = (await console.get(THE_MAIL)).json()
        postbox.refuses_login = (535, "Authentication Credentials Invalid")
        refused = await console.post(f"{THE_MAIL}/test", json={"to": "ana@clinica.test"})
        after = (await console.get(THE_MAIL)).json()
    assert nothing.status_code == 409
    assert went.json() == {"sent": True, "error": None}
    assert status["verified_at"] is not None
    assert status["last_error"] is None
    assert refused.status_code == 200
    assert refused.json()["sent"] is False
    assert "535" in refused.json()["error"]
    assert THE_MAILBOXS_SHH not in refused.text
    assert "535" in after["last_error"]


@postgres
async def test_an_org_with_its_own_mailbox_never_posts_through_the_boxs(
    knocking: Knocking, postbox: Postbox
) -> None:
    connections = knocking.gateway.connections
    boxs = mail.Mailbox("smtp.box.test", 587, "starttls", "box", "box-pass", "Box <b@box.test>")
    await mail.put_box_mail(connections.pool, connections.vault, boxs)
    async with knocking.http(knocking.app["production"]) as console:
        await console.put(THE_MAIL, json=A_MAILBOX)
        invited = await console.post(
            "/v1/members", json={"email": "bo@clinica.test", "name": "Bo", "role": "qa"}
        )
    assert invited.json()["mailed"] is True
    assert await delivered(knocking, postbox) == ["bo@clinica.test"]
    assert postbox.hosts == [("smtp.clinica.test", 587)]
    assert str(postbox.sent[0]["From"]) == THE_ORGS_SENDER


@postgres
async def test_the_orgs_policy_is_read_then_replaced_whole_and_says_who(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        first = await http.get("/v1/org/policy")
        put = await http.put("/v1/org/policy", json={"retention_days": 365})
        cleared = await http.put("/v1/org/policy", json={})
        refused = await http.put("/v1/org/policy", json={"retention_days": 0})
    nothing_set = {
        "retention_days": None,
        "calling_hours": None,
        "per_number_day": None,
        "consent_everywhere": False,
        "disclosure": None,
        "recording_notice": True,
    }
    assert first.json() == {"policy": nothing_set, "set_by": None, "set_at": None}
    assert put.status_code == 200
    assert put.json()["policy"] == {**nothing_set, "retention_days": 365}
    assert put.json()["set_by"]
    assert cleared.json()["policy"] == nothing_set
    assert refused.status_code == 422


@postgres
async def test_the_calling_hours_go_in_as_from_and_until_and_a_window_of_no_hour_is_refused(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        put = await http.put("/v1/org/policy", json={"calling_hours": {"from": 9, "until": 20}})
        empty = await http.put("/v1/org/policy", json={"calling_hours": {"from": 20, "until": 9}})
    assert put.json()["policy"]["calling_hours"] == {"from": 9, "until": 20}
    assert empty.status_code == 422


@postgres
async def test_the_export_is_a_download_of_json_lines_of_the_keys_world(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.get("/v1/org/export")
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in answer.headers["content-disposition"]
    header = json.loads(answer.text.splitlines()[0])
    assert (header["kind"], header["org"], header["env"]) == ("export", knocking.org.id, "sandbox")
    rows = await reads.of_org(knocking.gateway.connections.pool, knocking.org.id)
    assert [(row.subject, row.what, row.env) for row in rows] == [
        (knocking.org.id, "export", "sandbox")
    ]


@postgres
async def test_the_orgs_reads_are_listed_newest_first_and_of_one_call_when_named(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    await reads.record(pool, Scope(knocking.org.id), Read("CA_1", "recording", "m_ana"))
    await reads.record(pool, Scope(knocking.org.id), Read("CA_2", "log", reads.OPERATOR))
    async with knocking.http(knocking.app["production"]) as http:
        every = await http.get("/v1/org/reads")
        of_one = await http.get("/v1/org/reads", params={"subject": "CA_1"})
    assert [row["subject"] for row in every.json()["reads"]] == ["CA_2", "CA_1"]
    assert [(row["what"], row["reader"]) for row in of_one.json()["reads"]] == [
        ("recording", "m_ana")
    ]
