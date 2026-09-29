"""Tests for the box's own settings and floor: mail, brand, sign-in, routes, fleet, events."""

from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import AGENT, FLEETS, Knocking, postgres
from tests.fakes.mail import Postbox
from tests.gateway.api.conftest import a_call, first_data
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
        listed = await operator.get("/v1/ops/routes", params={"org": org})
        sandbox = await operator.get("/v1/ops/routes", params={"org": org, "env": "sandbox"})
        gone = await operator.delete("/v1/ops/routes/+59829001199", params={"org": org})
        again = await operator.delete("/v1/ops/routes/+59829001199", params={"org": org})
        nobody = await operator.get("/v1/ops/routes", params={"org": "nobody"})
    assert added.status_code == 200
    assert (added.json()["env"], added.json()["managed"]) == ("production", False)
    assert [route["number"] for route in listed.json()] == ["+59829001199"]
    assert sandbox.json() == []
    assert gone.status_code == 204
    assert again.status_code == 404
    assert nobody.status_code == 404


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
