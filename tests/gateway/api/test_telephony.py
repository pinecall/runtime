"""Tests for the numbers doors, knocked as the console knocks them, and the worker's leg."""

import pytest

from pinecall.domain.types import THE_FLEET, JsonObject, KeyScope
from pinecall.tenancy import vault
from tests.conftest import AGENT, BOX_DOMAIN, Knocking, issued, postgres, said_until, sent
from tests.fakes import Server, Twilio

A_NUMBER = "+13617334133"
HER_PHONE = "+59899000001"
HERE = f"sip:{BOX_DOMAIN}:5060;transport=udp"


def the_account(twilio: Twilio) -> dict[str, str]:
    """The body the console's Numbers screen sends for a Twilio account."""
    return {
        "kind": "twilio",
        "account_sid": twilio.account_sid,
        "user": twilio.user,
        "secret": twilio.secret,
    }


@postgres
async def test_a_carrier_is_brought_verified_listed_by_account_and_taken_back(
    gateway: Knocking, twilio: Twilio
) -> None:
    async with gateway.http(gateway.app["production"]) as console:
        assert (await console.get("/v1/carrier")).status_code == 404
        brought = await console.put("/v1/carrier", json=the_account(twilio))
        assert brought.status_code == 200
        shown = (await console.get("/v1/carrier")).json()
        assert shown == {"kind": "twilio", "account": twilio.account_sid, "label": ""}
        assert twilio.secret not in str(shown)
        listed = (await console.get("/v1/carriers")).json()
        assert [one["account"] for one in listed["carriers"]] == [twilio.account_sid]
        assert (await console.delete("/v1/carrier")).status_code == 204
        assert (await console.get("/v1/carrier")).status_code == 404


@postgres
async def test_a_bad_sid_and_a_pair_twilio_refuses_are_400(
    gateway: Knocking, twilio: Twilio
) -> None:
    async with gateway.http(gateway.app["production"]) as console:
        bad = await console.put("/v1/carrier", json={**the_account(twilio), "account_sid": "AC1"})
        refused = await console.put("/v1/carrier", json={**the_account(twilio), "secret": "no"})
    assert bad.status_code == 422
    assert refused.status_code == 400
    assert "Twilio refused" in refused.json()["detail"]


@postgres
async def test_the_screens_import_a_dry_run_then_the_import_and_the_listing(
    gateway: Knocking, twilio: Twilio
) -> None:
    twilio.owns(A_NUMBER)
    async with gateway.http(gateway.app["production"]) as console:
        await console.put("/v1/carrier", json=the_account(twilio))
        available = (await console.get("/v1/numbers/available")).json()
        assert available["kind"] == "twilio"
        assert available["numbers"] == [
            {"number": A_NUMBER, "name": A_NUMBER, "imported": False, "account": twilio.account_sid}
        ]
        wanted = {"number": A_NUMBER, "agent": AGENT, "channel": "phone"}
        plan = (await console.post("/v1/numbers?dry_run=true", json=wanted)).json()
        assert plan["dry_run"] is True
        assert twilio.trunks == {}
        done = await console.post("/v1/numbers", json=wanted)
        assert done.status_code == 200
        routed = done.json()
        assert routed["dry_run"] is False
        assert routed["route"] == {
            "org": gateway.org.id,
            "agent": AGENT,
            "channel": "phone",
            "number": A_NUMBER,
            "label": None,
            "env": "production",
            "managed": False,
        }
        (trunk,) = twilio.trunks.values()
        assert trunk.origination == [HERE]
        listing = (await console.get("/v1/numbers")).json()
        assert [one["route"]["number"] for one in listing] == [A_NUMBER]
        assert (await console.get("/v1/numbers/available")).json()["numbers"][0]["imported"]
        assert (await console.delete(f"/v1/numbers/{A_NUMBER}")).status_code == 204
        assert (await console.get("/v1/numbers")).json() == []
        assert (await console.delete(f"/v1/numbers/{A_NUMBER}")).status_code == 404


@postgres
async def test_the_refusals_name_what_is_missing(gateway: Knocking, twilio: Twilio) -> None:
    wanted = {"number": A_NUMBER, "agent": AGENT}
    async with gateway.http(gateway.app["production"]) as console:
        nothing = await console.post("/v1/numbers", json=wanted)
        await console.put("/v1/carrier", json=the_account(twilio))
        not_owned = await console.post("/v1/numbers", json=wanted)
        not_a_number = await console.post("/v1/numbers", json={**wanted, "number": "0991"})
    assert (nothing.status_code, not_owned.status_code, not_a_number.status_code) == (404, 404, 400)
    assert "PUT /v1/carrier" in nothing.json()["detail"]


@postgres
async def test_a_number_the_org_hooks_itself_moves_between_worlds_by_its_door(
    gateway: Knocking,
) -> None:
    async with gateway.http(gateway.app["production"]) as console:
        wanted = {"number": A_NUMBER, "agent": AGENT, "hooked": True}
        assert (await console.post("/v1/numbers", json=wanted)).status_code == 200
        moved = await console.put(f"/v1/numbers/{A_NUMBER}/env", json={"env": "sandbox"})
        assert moved.json()["route"]["env"] == "sandbox"
        assert (await console.get("/v1/numbers")).json() == []
    async with gateway.http(gateway.app["sandbox"]) as sandbox:
        assert [one["route"]["number"] for one in (await sandbox.get("/v1/numbers")).json()] == [
            A_NUMBER
        ]


@postgres
async def test_a_sandbox_key_buys_into_the_sandbox_on_the_boxs_account(
    gateway: Knocking, twilio: Twilio
) -> None:
    boxs: JsonObject = {
        "account_sid": twilio.account_sid,
        "user": twilio.user,
        "secret": twilio.secret,
    }
    await vault.put_box_credentials(gateway.box.pool, gateway.box.vault, "twilio", boxs)
    twilio.for_sale = [A_NUMBER]
    async with gateway.http(gateway.app["sandbox"]) as sandbox:
        wanted = {"country": "US", "agent": AGENT}
        plan = (await sandbox.post("/v1/numbers/buy?dry_run=true", json=wanted)).json()
        assert (plan["dry_run"], plan["route"]["number"]) == (True, A_NUMBER)
        bought = (await sandbox.post("/v1/numbers/buy", json=wanted)).json()
    assert (bought["route"]["env"], bought["route"]["managed"]) == ("sandbox", True)
    assert A_NUMBER in twilio.numbers


@postgres
async def test_the_outbound_screen_reads_what_is_missing_then_ready(
    gateway: Knocking, twilio: Twilio
) -> None:
    twilio.owns(A_NUMBER)
    async with gateway.http(gateway.app["production"]) as console:
        empty = (await console.get("/v1/carrier/outbound")).json()
        assert empty["ready"] is False
        assert set(empty["guards"]) == {"dial_anywhere", "per_minute", "per_day", "max_duration_s"}
        await console.put("/v1/carrier", json=the_account(twilio))
        await console.post("/v1/numbers", json={"number": A_NUMBER, "agent": AGENT})
        plan = (await console.post("/v1/carrier/outbound?dry_run=true")).json()
        assert set(plan) == {"steps", "dry_run", "ready"}
        done = (await console.post("/v1/carrier/outbound")).json()
        assert done["ready"] is True
        assert done["address"].endswith(".pstn.twilio.com")
        ready = (await console.get("/v1/carrier/outbound")).json()
    assert (ready["ready"], ready["kind"], ready["from_numbers"]) == (True, "twilio", [A_NUMBER])


@pytest.fixture
async def dialling(gateway: Knocking, twilio: Twilio) -> Knocking:
    """The org with an account, its number imported, provisioned, dialling anyone."""
    twilio.owns(A_NUMBER)
    async with gateway.http(gateway.app["production"]) as console:
        await console.put("/v1/carrier", json=the_account(twilio))
        await console.post("/v1/numbers", json={"number": A_NUMBER, "agent": AGENT})
        await console.post("/v1/carrier/outbound")
    async with gateway.box.pool.connection() as connection:
        await connection.execute(
            "insert into dial_policy (org, dial_anywhere) values (%s, true)", (gateway.org.id,)
        )
    return gateway


@postgres
async def test_a_call_back_is_202_with_its_call_and_a_log_token_and_nobody_holding_is_409(
    dialling: Knocking,
) -> None:
    async with dialling.http(dialling.app["production"]) as console:
        nobody = await console.post(f"/v1/agents/{AGENT}/dial", json={"to": HER_PHONE})
        app = await dialling.socket("/v1/apps", dialling.app["production"])
        await sent(app, "agent.register", {"routes": []})
        await said_until(app, "agent.registered")
        placed = await console.post(f"/v1/agents/{AGENT}/dial", json={"to": HER_PHONE})
        await app.close()
    assert nobody.status_code == 409
    assert placed.status_code == 202
    said = placed.json()
    assert set(said) == {"call", "agent", "to", "from", "env", "log_token"}
    assert (said["to"], said["from"], said["env"]) == (HER_PHONE, A_NUMBER, "production")
    server = dialling.box.server
    assert isinstance(server, Server)
    (dispatch,) = server.dispatcher.made
    assert (dispatch.room, dispatch.agent_name) == (said["call"], "pinecall")


@postgres
async def test_the_worker_is_told_the_legs_trunk_inline_after_the_guards(
    dialling: Knocking,
) -> None:
    scopes: frozenset[KeyScope] = frozenset({THE_FLEET})
    fleet = await issued(dialling.box.pool, "default", "production", scopes)
    asked = {"to": "+34910000000", "call": "call_1", "org": dialling.org.id, "env": "production"}
    async with dialling.http(fleet) as worker:
        leg = await worker.get(f"/v1/agents/{AGENT}/outbound-trunk", params=asked)
        bad = await worker.get(
            f"/v1/agents/{AGENT}/outbound-trunk", params={**asked, "to": "+8816000000"}
        )
    assert leg.status_code == 200
    trunk = leg.json()["trunk"]
    assert trunk["hostname"].endswith(".pstn.twilio.com")
    assert (trunk["shown"], trunk["transport"]) == (A_NUMBER, "auto")
    assert bad.status_code == 400
