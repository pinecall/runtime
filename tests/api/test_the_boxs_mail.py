"""The box's own mail from the operator's doors: stored wins over the environment, then tested."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.box_mail import NOTHING_STORED, NOTHING_TO_TEST
from pinecall.mail import Outbox
from pinecall.orgs.vault import NO_VAULT_KEY
from tests.api.mailing import A_BOX_SENDER
from tests.api.no_vault import OnABoxWithNoVaultKey
from tests.api.test_members_and_login import invited
from tests.mail.fake_smtp import FakeSmtp

pytestmark = pytest.mark.unit

THE_DOOR = "/v1/ops/mail"
A_PASSWORD_NOBODY_MAY_READ = "hunter2-and-then-some"


def wiring(server: FakeSmtp) -> dict[str, Any]:
    """What the operator PUTs: the org's own body, word for word (api/org_mail.py, WantedMail)."""
    return {
        "host": server.host,
        "port": server.port,
        "security": "none",
        "username": "AKIAEXAMPLE",
        "password": A_PASSWORD_NOBODY_MAY_READ,
        "from": "Acme Voice <no-reply@acme.test>",
    }


NOTHING = {
    "configured": False,
    "source": None,
    "host": None,
    "port": None,
    "security": None,
    "username": None,
    "from": None,
    "verified_at": None,
    "last_error": None,
}


async def test_a_box_told_nothing_reads_as_nothing_and_drops_nothing(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient
) -> None:
    assert (await ops_http.get(THE_DOOR)).json() == NOTHING
    gone = await ops_http.delete(THE_DOOR)
    assert (gone.status_code, gone.json()["detail"]) == (404, NOTHING_STORED)
    assert (await stranger.get("/.well-known/pinecall")).json()["mail"] is False


async def test_the_stored_mailbox_is_read_back_without_its_password_and_wins_over_the_environment(
    ops_http: httpx.AsyncClient,
    stranger: httpx.AsyncClient,
    tenant_http: httpx.AsyncClient,
    outbox: Outbox,
    relay: FakeSmtp,
    the_orgs_relay: FakeSmtp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment names one relay, the operator stores another: letters take the second."""
    monkeypatch.setattr(
        outbox.the_boxs, "_environment", relay.mailbox(sender=A_BOX_SENDER), raising=True
    )
    before = (await ops_http.get(THE_DOOR)).json()
    assert (before["configured"], before["source"], before["from"]) == (
        True,
        "environment",
        A_BOX_SENDER,
    )

    stored = await ops_http.put(THE_DOOR, json=wiring(the_orgs_relay))

    assert stored.status_code == 200, stored.text
    assert stored.json() == {
        **NOTHING,
        "configured": True,
        "source": "stored",
        "host": the_orgs_relay.host,
        "port": the_orgs_relay.port,
        "security": "none",
        "username": "AKIAEXAMPLE",
        "from": "Acme Voice <no-reply@acme.test>",
    }
    assert A_PASSWORD_NOBODY_MAY_READ not in stored.text
    assert (await stranger.get("/.well-known/pinecall")).json()["mail"] is True
    await invited(tenant_http)
    await outbox.drained()
    assert relay.took == [] and len(the_orgs_relay.took) == 1
    assert the_orgs_relay.took[0].message["From"] == "Acme Voice <no-reply@acme.test>"
    after = (await ops_http.get(THE_DOOR)).json()
    assert after["verified_at"] is not None and after["last_error"] is None
    # Dropped, the environment's answers again.
    assert (await ops_http.delete(THE_DOOR)).status_code == 204
    assert (await ops_http.get(THE_DOOR)).json()["source"] == "environment"


async def test_the_orgs_own_account_still_wins_over_the_boxs_stored_one(
    ops_http: httpx.AsyncClient,
    tenant_http: httpx.AsyncClient,
    outbox: Outbox,
    relay: FakeSmtp,
    the_orgs_relay: FakeSmtp,
) -> None:
    assert (await ops_http.put(THE_DOOR, json=wiring(relay))).status_code == 200
    assert (await tenant_http.put("/v1/org/mail", json=wiring(the_orgs_relay))).status_code == 200
    await invited(tenant_http)
    await outbox.drained()
    assert relay.took == [] and len(the_orgs_relay.took) == 1
    assert (await ops_http.get(THE_DOOR)).json()["verified_at"] is None, "not its letter"


async def test_the_test_send_waits_for_the_boxs_own_server_and_records_what_it_said(
    ops_http: httpx.AsyncClient, relay: FakeSmtp
) -> None:
    nothing = await ops_http.post(f"{THE_DOOR}/test", json={"to": "ops@acme.test"})
    assert (nothing.status_code, nothing.json()["detail"]) == (409, NOTHING_TO_TEST)
    assert (await ops_http.put(THE_DOOR, json=wiring(relay))).status_code == 200

    sent = await ops_http.post(f"{THE_DOOR}/test", json={"to": "ops@acme.test"})

    assert sent.json() == {"sent": True, "error": None}
    assert relay.took[0].message["Subject"] == "Pinecall test message"
    assert relay.took[0].message["To"] == "ops@acme.test"
    assert (await ops_http.get(THE_DOOR)).json()["verified_at"] is not None
    bad = await ops_http.post(f"{THE_DOOR}/test", json={"to": "not an address"})
    assert bad.status_code == 400


async def test_a_refused_letter_is_the_servers_sentence_on_the_row_and_never_the_password(
    ops_http: httpx.AsyncClient, relay: FakeSmtp
) -> None:
    relay.refuses_the_password = True
    assert (await ops_http.put(THE_DOOR, json=wiring(relay))).status_code == 200
    sent = await ops_http.post(f"{THE_DOOR}/test", json={"to": "ops@acme.test"})
    assert sent.json()["sent"] is False and sent.json()["error"]
    standing = (await ops_http.get(THE_DOOR)).json()
    assert standing["last_error"] == sent.json()["error"] and standing["verified_at"] is None
    assert A_PASSWORD_NOBODY_MAY_READ not in standing["last_error"]


async def test_a_bad_body_is_the_orgs_own_refusal_and_the_door_is_the_operators(
    ops_http: httpx.AsyncClient, tenant_http: httpx.AsyncClient, relay: FakeSmtp
) -> None:
    bad = await ops_http.put(THE_DOOR, json={**wiring(relay), "security": "magic"})
    assert bad.status_code == 400 and "starttls" in bad.json()["detail"]
    assert (await tenant_http.get(THE_DOOR)).status_code == 401
    assert (await tenant_http.put(THE_DOOR, json=wiring(relay))).status_code == 401


class TestWithNoVaultKey(OnABoxWithNoVaultKey):
    """A box with no vault key can keep no password, and says so with the vault's sentence."""

    async def test_storing_a_password_is_refused_503_and_the_brand_still_takes(
        self, ops_http: httpx.AsyncClient, relay: FakeSmtp
    ) -> None:
        refused = await ops_http.put(THE_DOOR, json=wiring(relay))
        assert (refused.status_code, refused.json()["detail"]) == (503, NO_VAULT_KEY)
        assert (await ops_http.put("/v1/ops/brand", json={"name": "Acme"})).status_code == 200


async def test_the_brand_is_pinecall_until_the_operator_says_and_then_it_is_theirs(
    ops_http: httpx.AsyncClient, stranger: httpx.AsyncClient, relay: FakeSmtp
) -> None:
    assert (await ops_http.get("/v1/ops/brand")).json() == {
        "name": "Pinecall",
        "logo_url": None,
        "accent": "#5b3df5",
    }
    set_ = await ops_http.put(
        "/v1/ops/brand",
        json={"name": "Acme Voice", "logo_url": "https://cdn.acme.test/mark.png"},
    )
    assert set_.json() == {
        "name": "Acme Voice",
        "logo_url": "https://cdn.acme.test/mark.png",
        "accent": "#5b3df5",
    }
    assert (await ops_http.put("/v1/ops/brand", json={"accent": "#FF6600"})).json()["accent"] == (
        "#ff6600"
    )
    cleared = await ops_http.put("/v1/ops/brand", json={"logo_url": ""})
    assert cleared.json()["logo_url"] is None and cleared.json()["name"] == "Acme Voice"
    for bad in (
        {"accent": "red"},
        {"logo_url": "http://cdn.acme.test/mark.png"},
        {"name": "x" * 61},
    ):
        assert (await ops_http.put("/v1/ops/brand", json=bad)).status_code == 400, bad
    assert (await stranger.get("/.well-known/pinecall")).json()["brand"] == {
        "name": "Acme Voice",
        "logo_url": None,
        "accent": "#ff6600",
    }
    # And the letters carry it from the next one on.
    assert (await ops_http.put(THE_DOOR, json=wiring(relay))).status_code == 200
    await ops_http.post(f"{THE_DOOR}/test", json={"to": "ops@acme.test"})
    assert relay.took[0].message["Subject"] == "Acme Voice test message"
    assert (await stranger.get("/v1/ops/brand")).status_code == 401
    # An empty name is the default again, as an empty logo is none: the way back.
    assert (await ops_http.put("/v1/ops/brand", json={"name": " "})).json()["name"] == "Pinecall"
