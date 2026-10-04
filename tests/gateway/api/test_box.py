"""Tests for the box's own settings and floor: mail, brand, sign-in, routes, fleet, events."""

from dataclasses import replace

from livekit.protocol.sip import ListSIPInboundTrunkRequest

from pinecall.channels import routes
from pinecall.channels.routes import RouteWrite
from pinecall.channels.telephony.carrier import TWILIO_SIGNALLING
from pinecall.domain.call import Route
from pinecall.tenancy import orgs
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import AGENT, FLEETS, Knocking, postgres
from tests.fakes.mail import Postbox
from tests.gateway.api.conftest import a_call, an_app, first_data
from tests.gateway.api.test_ops import THE_OPS_KEY, with_an_ops_key

A_MAILBOX = {
    "host": "smtp.box.test",
    "port": 587,
    "security": "starttls",
    "username": "box",
    "password": "box-pass",
    "from": "Pinecall <no-reply@box.test>",
}


@postgres
async def test_the_boxs_mailbox_is_stored_over_the_environments_and_a_letter_goes_through_it(
    knocking: Knocking, postbox: Postbox
) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        before = await operator.get("/v1/ops/mail")
        untested = await operator.post("/v1/ops/mail/test", json={"to": "me@clinica.test"})
        stored = await operator.put("/v1/ops/mail", json=A_MAILBOX)
        tested = await operator.post("/v1/ops/mail/test", json={"to": "me@clinica.test"})
        dropped = await operator.delete("/v1/ops/mail")
        again = await operator.delete("/v1/ops/mail")
    assert before.json()["configured"] is False
    assert untested.status_code == 409
    assert (stored.json()["source"], stored.json()["host"]) == ("stored", "smtp.box.test")
    assert "box-pass" not in stored.text
    assert tested.json() == {"sent": True, "error": None}
    assert [str(letter["To"]) for letter in postbox.sent] == ["me@clinica.test"]
    assert dropped.status_code == 204
    assert again.status_code == 404


@postgres
async def test_the_brand_changes_field_by_field_and_an_empty_field_goes_back(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        default = await operator.get("/v1/ops/brand")
        named = await operator.put(
            "/v1/ops/brand", json={"name": "Clinica", "logo_url": "https://c.test/logo.png"}
        )
        painted = await operator.put("/v1/ops/brand", json={"accent": "#0A7B83"})
        cleared = await operator.put("/v1/ops/brand", json={"logo_url": ""})
        refused = await operator.put("/v1/ops/brand", json={"logo_url": "http://plain.test/x"})
    assert default.json()["name"] == "Pinecall"
    assert (named.json()["name"], named.json()["logo_url"]) == (
        "Clinica",
        "https://c.test/logo.png",
    )
    assert (painted.json()["accent"], painted.json()["name"]) == ("#0a7b83", "Clinica")
    assert cleared.json()["logo_url"] is None
    assert refused.status_code == 400


async def admitting(knocking: Knocking) -> list[tuple[str, list[str]]]:
    """Every inbound trunk on the box's SFU, by name, with the numbers it admits."""
    listed = await knocking.gateway.connections.server.sip.list_inbound_trunk(
        ListSIPInboundTrunkRequest()
    )
    return [(trunk.name, list(trunk.numbers)) for trunk in listed.items]


@postgres
async def test_box_wide_sign_in_is_named_and_refused_in_this_version(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        listed = await operator.get("/v1/ops/signin")
        wired = await operator.put(
            "/v1/ops/signin/google", json={"client_id": "c", "client_secret": "s"}
        )
        dropped = await operator.delete("/v1/ops/signin/google")
    google = listed.json()["google"]
    assert google["configured"] is False
    assert google["redirect_uri"].endswith("/v1/login/google/callback")
    assert (wired.status_code, dropped.status_code) == (503, 503)


@postgres
async def test_the_operator_types_lists_and_forgets_a_route(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    org = knocking.org.id
    async with knocking.http(THE_OPS_KEY) as operator:
        added = await operator.post(
            "/v1/ops/routes",
            json={"org": org, "number": "+59829001199", "agent": AGENT, "channel": "phone"},
        )
        admitted = await admitting(knocking)
        listed = await operator.get("/v1/ops/routes", params={"org": org})
        sandbox = await operator.get("/v1/ops/routes", params={"org": org, "env": "sandbox"})
        gone = await operator.delete("/v1/ops/routes/+59829001199", params={"org": org})
        again = await operator.delete("/v1/ops/routes/+59829001199", params={"org": org})
        nobody = await operator.get("/v1/ops/routes", params={"org": "nobody"})
    assert added.status_code == 200
    assert (added.json()["env"], added.json()["managed"]) == ("production", False)
    assert admitted == [(org, ["+59829001199"])]
    assert [route["number"] for route in listed.json()] == ["+59829001199"]
    assert sandbox.json() == []
    assert gone.status_code == 204
    assert await admitting(knocking) == []
    assert again.status_code == 404
    assert nobody.status_code == 404


@postgres
async def test_a_number_another_org_answers_is_refused_before_anything_is_written(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    other = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    wanted = {"number": "+59829001199", "agent": AGENT, "channel": "phone"}
    async with knocking.http(THE_OPS_KEY) as operator:
        first = await operator.post("/v1/ops/routes", json={**wanted, "org": other.id})
        second = await operator.post("/v1/ops/routes", json={**wanted, "org": knocking.org.id})
        theirs = await operator.get("/v1/ops/routes", params={"org": knocking.org.id})
    assert first.status_code == 200
    assert second.status_code == 409
    assert "another org's trunk" in second.text
    assert theirs.json() == []


@postgres
async def test_the_box_lists_every_number_how_it_came_and_whether_it_is_answered(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    pool = knocking.gateway.connections.pool
    other = await orgs.create(pool, "otra", "Otra")
    bought = Route(
        org=knocking.org.id, agent=AGENT, channel="phone", number="+59829001199", env="sandbox"
    )
    await routes.put(pool, replace(bought, managed=True), RouteWrite("bought"))
    await routes.put(
        pool, replace(bought, agent="nobody", number="+59829001100"), RouteWrite("hooked")
    )
    await routes.put(pool, replace(bought, org=other.id), RouteWrite("typed"))
    socket = await an_app(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        listed = await operator.get("/v1/ops/numbers")
    await socket.close()
    slug = knocking.org.slug
    assert [
        (row["number"], row["org"], row["came_in"], row["running"], row["answered_by"])
        for row in listed.json()
    ] == [
        ("+59829001100", slug, "hooked", False, None),
        ("+59829001199", slug, "bought", True, None),
        ("+59829001199", "otra", "typed", False, slug),
    ]


@postgres
async def test_the_operator_admits_a_carrier_and_twilio_stays_admitted(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        listed = await operator.get("/v1/ops/carriers")
        admitted = await operator.put("/v1/ops/carriers/telnyx", json={"admitted": True})
        kept = await operator.put("/v1/ops/carriers/twilio", json={"admitted": False})
        unknown = await operator.put("/v1/ops/carriers/bandwidth", json={"admitted": True})
        after = (await operator.get("/v1/ops/carriers")).json()
    by_kind = {item["kind"]: item for item in listed.json()["carriers"]}
    assert (by_kind["twilio"]["admitted"], by_kind["twilio"]["fixed"]) == (True, True)
    assert by_kind["telnyx"]["admitted"] is False
    assert admitted.json()["admitted"] is True
    assert (kept.status_code, unknown.status_code) == (409, 404)
    assert len(after["fence"]["openings"]) == 12
    assert {opening["reason"] for opening in after["fence"]["openings"]} == {"telnyx"}
    networks = after["fence"]["networks"]
    assert networks[: len(TWILIO_SIGNALLING)] == list(TWILIO_SIGNALLING)
    assert networks[len(TWILIO_SIGNALLING) :] == [o["network"] for o in after["fence"]["openings"]]


@postgres
async def test_the_operator_alone_sends_the_trunks_on_and_a_box_at_its_names_moves_none(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        moved = await operator.post("/v1/ops/sip/repoint")
    async with knocking.http(knocking.app["production"]) as tenant:
        refused = await tenant.post("/v1/ops/sip/repoint")
    assert (moved.status_code, moved.json(), refused.status_code) == (200, [], 401)


@postgres
async def test_a_peers_network_is_approved_by_the_operator_and_its_number_is_admitted(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    peer = {"kind": "sip", "username": "pbx", "password": "a peer's password"}
    number = {"number": "+59829001199", "agent": AGENT, "account": "pbx"}
    async with knocking.http(knocking.app["production"]) as console:
        await console.put("/v1/carrier", json={**peer, "addresses": ["45.60.12.7"]})
        waiting = await console.post("/v1/numbers", json=number)
    before = await admitting(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        (request,) = (
            await operator.get("/v1/ops/carrier-networks", params={"state": "waiting"})
        ).json()
        approved = await operator.post(f"/v1/ops/carrier-networks/{request['id']}/approve")
        admitted = await admitting(knocking)
        fence = (await operator.get("/v1/ops/carriers")).json()["fence"]
        refused = await operator.post(f"/v1/ops/carrier-networks/{request['id']}/refuse")
        missing = await operator.post("/v1/ops/carrier-networks/999999/approve")
    assert any("waits for the box's operator" in step for step in waiting.json()["steps"])
    assert before == []
    assert (request["org"], request["source"], request["network"]) == (
        knocking.org.slug,
        "pbx",
        "45.60.12.7/32",
    )
    assert (approved.json()["state"], approved.json()["decided_by"]) == (
        "approved",
        "the box's key",
    )
    assert admitted == [(f"{knocking.org.id}:pbx", ["+59829001199"])]
    assert fence["openings"] == [
        {"network": "45.60.12.7/32", "reason": f"{knocking.org.slug}: pbx"}
    ]
    assert refused.json()["state"] == "refused"
    assert await admitting(knocking) == []
    assert missing.status_code == 404


@postgres
async def test_the_fleet_lists_every_worker_heard_from_and_a_cordon_reaches_it(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    beat = {
        "fleet": FLEETS["sandbox"],
        "worker": "w-1",
        "active": 1,
        "max_jobs": 4,
        "load": 0.2,
        "draining": False,
    }
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/fleet/heartbeat", json=beat)
    async with knocking.http(THE_OPS_KEY) as operator:
        listed = await operator.get("/v1/ops/fleet")
        cordoned = await operator.post("/v1/ops/fleet/w-1/cordon")
        nobody = await operator.post("/v1/ops/fleet/w-9/cordon")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        answered = await worker.post("/v1/fleet/heartbeat", json=beat)
    async with knocking.http(THE_OPS_KEY) as operator:
        lifted = await operator.delete(
            "/v1/ops/fleet/w-1/cordon", params={"fleet": FLEETS["sandbox"]}
        )
    assert listed.status_code == 200
    assert [row["worker"] for row in listed.json()["workers"]] == ["w-1"]
    assert {row["fleet"] for row in listed.json()["totals"]} == set(FLEETS.values())
    assert listed.json()["stale_after_s"] == 30.0
    assert (cordoned.status_code, nobody.status_code) == (204, 404)
    assert answered.json()["cordoned"] is True
    assert lifted.status_code == 204


# KEDA's question: the core's two full workers ask for one scaled worker of 32 seats, no more.
@postgres
async def test_kubernetes_is_told_the_scaled_workers_the_fleet_wants(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        for name in ("worker-core-a", "worker-core-b"):
            beat = {
                "fleet": FLEETS["sandbox"],
                "worker": name,
                "active": 2,
                "max_jobs": 2,
                "load": 1.0,
                "draining": False,
            }
            await worker.post("/v1/fleet/heartbeat", json=beat)
    path = f"/v1/ops/fleet/{FLEETS['sandbox']}/wanted"
    async with knocking.http(THE_OPS_KEY) as operator:
        wanted = await operator.get(path, params={"scaled": "worker-burst-", "seats": 32})
        unsaid = await operator.get(path)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        refused = await worker.get(path, params={"scaled": "worker-burst-", "seats": 32})
    assert wanted.json() == {"fleet": FLEETS["sandbox"], "wanted": 1, "active": 4, "seats": 4}
    assert (unsaid.status_code, refused.status_code) == (422, 401)


@postgres
async def test_every_orgs_floor_streams_to_the_operator_with_the_org_and_world_named(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    context = a_call(knocking)
    async with (
        knocking.http(THE_OPS_KEY) as operator,
        operator.stream("GET", "/v1/ops/events", headers={"accept": "text/event-stream"}) as feed,
        knocking.http(knocking.fleet["sandbox"]) as worker,
    ):
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        data = await first_data(feed.aiter_lines())
    assert f'"org":"{knocking.org.id}"' in data
    assert '"env":"sandbox"' in data
    assert '"type":"call.ringing"' in data


@postgres
async def test_the_boxs_usage_pages_every_org_with_totals_per_org(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="done").written(),
        )
    async with knocking.http(THE_OPS_KEY) as operator:
        page = await operator.get("/v1/ops/usage")
        filtered = await operator.get("/v1/ops/usage", params={"org": knocking.org.slug})
        other = await operator.get("/v1/ops/usage", params={"org": "nobody"})
    assert [row["type"] for row in page.json()["rows"]] == ["call.summary", "call.score"]
    assert page.json()["totals"][knocking.org.id]["calls"] == 1
    assert len(filtered.json()["rows"]) == 2
    assert (other.json()["rows"], other.json()["next"]) == ([], page.json()["next"])


@postgres
async def test_the_providers_row_is_read_and_written_whole_and_a_stranger_vendor_refused(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        read = await operator.get("/v1/ops/providers")
        row = read.json()
        row["hints"] = ["es", "pt"]
        written = await operator.put("/v1/ops/providers", json=row)
        again = await operator.get("/v1/ops/providers")
        row["defaults"]["llm"] = {"vendor": "nobody"}
        refused = await operator.put("/v1/ops/providers", json=row)
    assert read.json()["defaults"]["llm"]["vendor"] == "acme"
    assert written.status_code == 200
    assert again.json()["hints"] == ["es", "pt"]
    assert refused.status_code == 400


@postgres
async def test_the_box_holds_and_drops_a_vendors_key_and_never_shows_it(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        listed = await operator.get("/v1/ops/provider-keys")
        dropped = await operator.delete("/v1/ops/provider-keys/acme")
        again = await operator.delete("/v1/ops/provider-keys/acme")
        put = await operator.put("/v1/ops/provider-keys/acme", json={"key": "the-boxs-own"})
        after = await operator.get("/v1/ops/provider-keys")
    assert listed.json() == {"vendors": ["acme"]}
    assert (dropped.status_code, again.status_code, put.status_code) == (204, 404, 204)
    assert "the-boxs-own" not in after.text


@postgres
async def test_admission_and_the_fleets_are_the_boxs_rows_set_whole(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    allowed = {"first": {"sandbox": {"minutes": 60}}, "later": {"sandbox": {"minutes": 0}}}
    async with knocking.http(THE_OPS_KEY) as operator:
        empty = await operator.get("/v1/ops/admission")
        put = await operator.put("/v1/ops/admission", json=allowed)
        made = await operator.post("/v1/ops/orgs", json={"slug": "nueva"})
        fleets = await operator.put(
            "/v1/ops/fleets", json={"production": "pinecall", "sandbox": "pinecall-dev"}
        )
        read = await operator.get("/v1/ops/fleets")
        profile = await operator.get(f"/v1/ops/orgs/{made.json()['id']}")
    assert empty.json() == {"first": {}, "later": None}
    assert put.json()["first"]["sandbox"]["minutes"] == 60
    assert profile.json()["quotas"]["sandbox"]["limits"]["minutes"] == 60
    assert (fleets.status_code, read.json()["sandbox"]) == (200, "pinecall-dev")
