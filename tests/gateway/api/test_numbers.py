"""Tests for the numbers doors, knocked as the console knocks them, and the worker's leg."""

import pytest

from pinecall.channels.telephony import carrier_catalog
from pinecall.domain.names import JsonObject
from pinecall.domain.person import THE_FLEET, KeyScope
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from pinecall.tenancy import vault
from tests.conftest import (
    AGENT,
    BOX_DOMAIN,
    Knocking,
    a_worker_heard,
    issued,
    postgres,
    received_until,
    sent,
)
from tests.fakes.livekit import Server
from tests.fakes.twilio import Twilio
from tests.gateway.api.conftest import an_app

A_NUMBER = "+15550100133"
HER_PHONE = "+59899000001"
HERE = f"sip:{BOX_DOMAIN}:5060;transport=udp"


def account_body(twilio: Twilio) -> dict[str, str]:
    """The body the console's Numbers screen sends for a Twilio account."""
    return {
        "kind": "twilio",
        "account_sid": twilio.account_sid,
        "user": twilio.user,
        "secret": twilio.secret,
    }


@postgres
async def test_a_carrier_is_brought_verified_listed_by_account_and_taken_back(
    knocking: Knocking, twilio: Twilio
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        assert (await console.get("/v1/carrier")).status_code == 404
        brought = await console.put("/v1/carrier", json=account_body(twilio))
        assert brought.status_code == 200
        shown = (await console.get("/v1/carrier")).json()
        assert shown == {
            "kind": "twilio",
            "account": twilio.account_sid,
            "label": "",
            "networks": [],
        }
        assert twilio.secret not in str(shown)
        listed = (await console.get("/v1/carriers")).json()
        assert [item["account"] for item in listed["carriers"]] == [twilio.account_sid]
        assert (await console.delete("/v1/carrier")).status_code == 204
        assert (await console.get("/v1/carrier")).status_code == 404


@postgres
async def test_the_catalog_offers_twilio_always_and_another_carrier_once_admitted(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        before = (await console.get("/v1/carriers/catalog")).json()
        await carrier_catalog.admit(knocking.gateway.connections.pool, "telnyx", on=True)
        after = (await console.get("/v1/carriers/catalog")).json()
    assert [(item["kind"], item["how"]) for item in before["carriers"]] == [("twilio", "automatic")]
    assert [(item["kind"], item["how"]) for item in after["carriers"]] == [
        ("twilio", "automatic"),
        ("telnyx", "guided"),
    ]
    assert after["carriers"][1]["networks"][0] == "192.76.120.10/32"
    assert before["sells"] is False


@postgres
async def test_a_number_via_a_carrier_the_box_admits_is_fenced_to_its_networks(
    knocking: Knocking,
) -> None:
    wanted = {"number": A_NUMBER, "agent": AGENT, "hooked": True, "via": "telnyx"}
    async with knocking.http(knocking.app["production"]) as console:
        refused = await console.post("/v1/numbers", json=wanted)
        await carrier_catalog.admit(knocking.gateway.connections.pool, "telnyx", on=True)
        hooked = await console.post("/v1/numbers", json=wanted)
        with_networks = await console.post(
            "/v1/numbers", json={**wanted, "networks": ["45.60.12.7"]}
        )
    assert refused.status_code == 409
    assert "does not admit Telnyx" in refused.text
    assert hooked.status_code == 200
    assert any("trunk org" in step and ":telnyx admits" in step for step in hooked.json()["steps"])
    assert with_networks.status_code == 400


@postgres
async def test_a_peers_networks_wait_for_the_operator_and_a_wide_one_is_refused(
    knocking: Knocking,
) -> None:
    peer = {"kind": "sip", "username": "pbx", "password": "a peer's password"}
    async with knocking.http(knocking.app["production"]) as console:
        wide = await console.put("/v1/carrier", json={**peer, "addresses": ["45.60.0.0/16"]})
        brought = await console.put("/v1/carrier", json={**peer, "addresses": ["45.60.12.7"]})
        listed = (await console.get("/v1/carriers")).json()
    assert wide.status_code == 400
    assert brought.json()["networks"] == [{"network": "45.60.12.7/32", "state": "waiting"}]
    assert listed["carriers"][0]["networks"][0]["state"] == "waiting"


@postgres
async def test_each_number_says_if_it_rings_now_and_its_path_says_why(
    knocking: Knocking, twilio: Twilio
) -> None:
    twilio.owns(A_NUMBER)
    wanted = {"number": A_NUMBER, "agent": AGENT, "channel": "phone"}
    async with knocking.http(knocking.app["production"]) as console:
        await console.put("/v1/carrier", json=account_body(twilio))
        await console.post("/v1/numbers", json=wanted)
        nobody = (await console.get("/v1/numbers")).json()
        app = await an_app(knocking, env="production")
        running = (await console.get("/v1/numbers")).json()
        path = (await console.get(f"/v1/numbers/{A_NUMBER}/path")).json()
        missing = await console.get("/v1/numbers/+15550100199/path")
    await app.close()
    assert (nobody[0]["rings"], nobody[0]["last_call_at"]) == ("broken", None)
    assert nobody[0]["account"] == twilio.account_sid
    assert running[0]["rings"] == "ok"
    assert [(step["step"], step["state"]) for step in path["steps"]] == [
        ("carrier", "ok"),
        ("fence", "ok"),
        ("world", "ok"),
        ("agent", "ok"),
    ]
    assert path["rings"] == "ok"
    assert missing.status_code == 404


@postgres
async def test_a_bad_sid_and_a_pair_twilio_refuses_are_400(
    knocking: Knocking, twilio: Twilio
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        bad = await console.put("/v1/carrier", json={**account_body(twilio), "account_sid": "AC1"})
        refused = await console.put("/v1/carrier", json={**account_body(twilio), "secret": "no"})
    assert bad.status_code == 422
    assert refused.status_code == 400
    assert "Twilio refused" in refused.json()["detail"]


@postgres
async def test_the_screens_import_a_dry_run_then_the_import_and_the_listing(
    knocking: Knocking, twilio: Twilio
) -> None:
    twilio.owns(A_NUMBER)
    async with knocking.http(knocking.app["production"]) as console:
        await console.put("/v1/carrier", json=account_body(twilio))
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
            "org": knocking.org.id,
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
        assert [(item["route"]["number"], item["origin"]) for item in listing] == [
            (A_NUMBER, "imported")
        ]
        assert (await console.get("/v1/numbers/available")).json()["numbers"][0]["imported"]
        assert (await console.delete(f"/v1/numbers/{A_NUMBER}")).status_code == 204
        assert (await console.get("/v1/numbers")).json() == []
        assert (await console.delete(f"/v1/numbers/{A_NUMBER}")).status_code == 404


@postgres
async def test_the_refusals_name_what_is_missing(knocking: Knocking, twilio: Twilio) -> None:
    wanted = {"number": A_NUMBER, "agent": AGENT}
    async with knocking.http(knocking.app["production"]) as console:
        nothing = await console.post("/v1/numbers", json=wanted)
        await console.put("/v1/carrier", json=account_body(twilio))
        not_owned = await console.post("/v1/numbers", json=wanted)
        not_a_number = await console.post("/v1/numbers", json={**wanted, "number": "0991"})
    assert (nothing.status_code, not_owned.status_code, not_a_number.status_code) == (404, 404, 400)
    assert "PUT /v1/carrier" in nothing.json()["detail"]


@postgres
async def test_a_number_the_org_hooks_itself_moves_between_worlds_by_its_door(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        wanted = {"number": A_NUMBER, "agent": AGENT, "hooked": True}
        assert (await console.post("/v1/numbers", json=wanted)).status_code == 200
        moved = await console.put(f"/v1/numbers/{A_NUMBER}/env", json={"env": "sandbox"})
        assert moved.json()["route"]["env"] == "sandbox"
        assert (await console.get("/v1/numbers")).json() == []
    async with knocking.http(knocking.app["sandbox"]) as sandbox:
        assert [item["route"]["number"] for item in (await sandbox.get("/v1/numbers")).json()] == [
            A_NUMBER
        ]


@postgres
async def test_a_sandbox_key_buys_into_the_sandbox_on_the_boxs_account(
    knocking: Knocking, twilio: Twilio
) -> None:
    boxs: JsonObject = {
        "account_sid": twilio.account_sid,
        "user": twilio.user,
        "secret": twilio.secret,
    }
    await vault.put_box_credentials(
        knocking.gateway.connections.pool, knocking.gateway.connections.vault, "twilio", boxs
    )
    twilio.for_sale = [A_NUMBER]
    async with knocking.http(knocking.app["sandbox"]) as sandbox:
        wanted = {"country": "US", "agent": AGENT}
        plan = (await sandbox.post("/v1/numbers/buy?dry_run=true", json=wanted)).json()
        assert (plan["dry_run"], plan["route"]["number"]) == (True, A_NUMBER)
        bought = (await sandbox.post("/v1/numbers/buy", json=wanted)).json()
    assert (bought["route"]["env"], bought["route"]["managed"]) == ("sandbox", True)
    assert A_NUMBER in twilio.numbers


@postgres
async def test_the_outbound_screen_reads_what_is_missing_then_ready(
    knocking: Knocking, twilio: Twilio
) -> None:
    twilio.owns(A_NUMBER)
    async with knocking.http(knocking.app["production"]) as console:
        empty = (await console.get("/v1/carrier/outbound")).json()
        assert empty["ready"] is False
        assert set(empty["guards"]) == {"dial_anywhere", "per_minute", "per_day", "max_duration_s"}
        await console.put("/v1/carrier", json=account_body(twilio))
        await console.post("/v1/numbers", json={"number": A_NUMBER, "agent": AGENT})
        plan = (await console.post("/v1/carrier/outbound?dry_run=true")).json()
        assert set(plan) == {"steps", "dry_run", "ready"}
        done = (await console.post("/v1/carrier/outbound")).json()
        assert done["ready"] is True
        assert done["address"].endswith(".pstn.twilio.com")
        ready = (await console.get("/v1/carrier/outbound")).json()
    assert (ready["ready"], ready["kind"], ready["from_numbers"]) == (True, "twilio", [A_NUMBER])


@pytest.fixture
async def dialling(knocking: Knocking, twilio: Twilio) -> Knocking:
    """The org with an account, its number imported, provisioned, dialling anyone."""
    twilio.owns(A_NUMBER)
    async with knocking.http(knocking.app["production"]) as console:
        await console.put("/v1/carrier", json=account_body(twilio))
        await console.post("/v1/numbers", json={"number": A_NUMBER, "agent": AGENT})
        await console.post("/v1/carrier/outbound")
    async with knocking.gateway.connections.pool.connection() as connection:
        await connection.execute(
            "insert into dial_policy (org, dial_anywhere) values (%s, true)", (knocking.org.id,)
        )
    return knocking


@postgres
async def test_a_call_back_is_202_with_its_call_and_a_log_token_and_nobody_holding_is_409(
    dialling: Knocking,
) -> None:
    a_worker_heard(dialling.gateway.roster, "pinecall")
    async with dialling.http(dialling.app["production"]) as console:
        nobody = await console.post(f"/v1/agents/{AGENT}/dial", json={"to": HER_PHONE})
        app = await dialling.socket("/v1/apps", dialling.app["production"])
        await sent(app, "agent.register", {"routes": []})
        await received_until(app, "agent.registered")
        placed = await console.post(f"/v1/agents/{AGENT}/dial", json={"to": HER_PHONE})
        await app.close()
    assert nobody.status_code == 409
    assert placed.status_code == 202
    answer = placed.json()
    assert set(answer) == {"call", "agent", "to", "from", "env", "log_token"}
    assert (answer["to"], answer["from"], answer["env"]) == (HER_PHONE, A_NUMBER, "production")
    server = dialling.gateway.connections.servers["production"]
    assert isinstance(server, Server)
    (dispatch,) = server.dispatcher.made
    assert (dispatch.room, dispatch.agent_name) == (answer["call"], "pinecall/w1")


@postgres
async def test_the_worker_is_told_the_legs_trunk_inline_after_the_guards(
    dialling: Knocking,
) -> None:
    scopes: frozenset[KeyScope] = frozenset({THE_FLEET})
    fleet = await issued(dialling.gateway.connections.pool, "default", "production", scopes)
    # The leg is asked for a call the worker opened: its head names the org and the world.
    opened = Claim(Scope(dialling.org.id, "production"))
    await dialling.gateway.logs.store.claim("call_1", AGENT, dialling.org.id, opened)
    params = {"to": "+34910000000", "call": "call_1", "org": dialling.org.id, "env": "production"}
    async with dialling.http(fleet) as worker:
        leg = await worker.get(f"/v1/agents/{AGENT}/outbound-trunk", params=params)
        bad = await worker.get(
            f"/v1/agents/{AGENT}/outbound-trunk", params={**params, "to": "+8816000000"}
        )
    assert leg.status_code == 200
    trunk = leg.json()["trunk"]
    assert trunk["hostname"].endswith(".pstn.twilio.com")
    assert (trunk["shown"], trunk["transport"]) == (A_NUMBER, "auto")
    assert bad.status_code == 400


@postgres
async def test_a_consent_is_recorded_read_back_and_an_opt_out_outranks_it(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        given = await http.post(
            "/v1/org/consents",
            json={"number": HER_PHONE, "kind": "express", "source": "the booking form"},
        )
        dropped = await http.delete(f"/v1/org/consents/{HER_PHONE}")
        read = await http.get(f"/v1/org/consents/{HER_PHONE}")
        listed = await http.get("/v1/org/dnc")
    assert (given.status_code, given.json()["standing"]) == (200, "consented")
    assert dropped.json()["standing"] == "opted_out"
    assert [row["kind"] for row in read.json()["rows"]] == ["opt_out", "express"]
    assert [opted["number"] for opted in listed.json()["numbers"]] == [HER_PHONE]


@postgres
async def test_a_list_is_imported_whole_and_a_line_that_is_no_number_is_said(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        imported = await http.post(
            "/v1/org/dnc",
            json={"numbers": [HER_PHONE, "+59899000002", "call me"], "source": "our own list"},
        )
        listed = await http.get("/v1/org/dnc")
    assert imported.json() == {"added": 2, "refused": ["call me"]}
    assert len(listed.json()["numbers"]) == 2


# A cold transfer dials nothing of the box's: the door judges the leg alone, an org with no
# outbound account included, and refuses a shape no carrier dials.
@postgres
async def test_a_cold_transfers_leg_is_judged_with_no_trunk_and_a_bad_shape_refused(
    knocking: Knocking,
) -> None:
    scopes: frozenset[KeyScope] = frozenset({THE_FLEET})
    fleet = await issued(knocking.gateway.connections.pool, "default", "sandbox", scopes)
    opened = Claim(Scope(knocking.org.id, "sandbox"))
    await knocking.gateway.logs.store.claim("call_1", AGENT, knocking.org.id, opened)
    params = {"to": "+34910000000", "call": "call_1", "org": knocking.org.id, "env": "sandbox"}
    async with knocking.http(fleet) as worker:
        judged = await worker.post(f"/v1/agents/{AGENT}/cold-transfer", params=params)
        bad = await worker.post(
            f"/v1/agents/{AGENT}/cold-transfer", params={**params, "to": "sip:x@10.0.0.1"}
        )
    assert judged.status_code == 204
    assert bad.status_code == 400
