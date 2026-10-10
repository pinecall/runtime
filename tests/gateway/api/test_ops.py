"""Tests for the box's own doors over its orgs: who runs it, the orgs, people, keys, tracebacks."""

from dataclasses import replace

from pinecall.channels import routes
from pinecall.channels.routes import RouteWrite
from pinecall.domain.call import Route
from pinecall.gateway.app import app
from pinecall.tenancy import erasure, people, sso
from pinecall.tenancy.sso import Client, OrgSso
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, a_developer, postgres
from tests.fakes.acme import ACME
from tests.gateway.api.conftest import a_call
from tests.log.conftest import ACall, logged_call

THE_OPS_KEY = "the-operators-own-key-of-this-box"
ORGS = "/v1/ops/orgs"


def with_an_ops_key(knocking: Knocking) -> None:
    """The same gateway, its operator key set."""
    settings = knocking.gateway.connections.settings.model_copy(update={"ops_key": THE_OPS_KEY})
    connections = replace(knocking.gateway.connections, settings=settings)
    app.state.gateway = replace(knocking.gateway, connections=connections)


@postgres
async def test_the_boxs_own_key_opens_it_and_names_nobody(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        answer = await operator.get("/v1/ops/whoami")
    body = answer.json()
    assert answer.status_code == 200
    assert (body["operator"], body["domain"], body["name"], body["org"]) == (
        True,
        "box.test",
        None,
        None,
    )
    assert body["version"]
    assert THE_OPS_KEY not in answer.text


@postgres
async def test_a_person_the_box_made_an_operator_is_named_with_their_org(
    knocking: Knocking,
) -> None:
    member, secret = await a_developer(knocking, "ana@clinica.test")
    await people.make_operator(knocking.gateway.connections.pool, knocking.org.id, member, on=True)
    async with knocking.http(secret) as operator:
        body = (await operator.get("/v1/ops/whoami")).json()
    assert (body["name"], body["org"]) == ("ana", knocking.org.id)


@postgres
async def test_the_ops_doors_take_the_ops_key_and_nothing_else(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    _, developer = await a_developer(knocking, "bo@clinica.test")
    async with knocking.http(knocking.app["production"]) as server:
        by_a_server = await server.get("/v1/ops/whoami")
    async with knocking.http(developer) as person:
        by_a_person = await person.get("/v1/ops/whoami")
    async with knocking.http("the-wrong-ops-key") as wrong:
        by_a_guess = await wrong.get("/v1/ops/whoami")
    assert [by_a_server.status_code, by_a_person.status_code, by_a_guess.status_code] == [401] * 3


@postgres
async def test_an_org_is_made_listed_read_and_forgotten_once_nothing_names_it(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        made = await operator.post(ORGS, json={"slug": "tienda", "name": "La Tienda"})
        taken = await operator.post(ORGS, json={"slug": "tienda"})
        bad = await operator.post(ORGS, json={"slug": "La Tienda"})
        listed = await operator.get(ORGS)
        profile = await operator.get(f"{ORGS}/tienda")
        gone = await operator.delete(f"{ORGS}/tienda")
        nobody = await operator.get(f"{ORGS}/tienda")
        busy = await operator.delete(f"{ORGS}/{knocking.org.id}")
    assert made.status_code == 201
    assert (made.json()["slug"], made.json()["name"]) == ("tienda", "La Tienda")
    assert made.json()["id"].startswith("org_")
    assert taken.status_code == 409
    assert bad.status_code == 400
    assert [org["slug"] for org in listed.json()][-1] == "tienda"
    body = profile.json()
    assert body["slug"] == "tienda"
    assert set(body["quotas"]) == {"production", "sandbox"}
    assert body["holding"] == {"memory_facts": 0, "knowledge_chunks": 0, "numbers": 0, "seats": 0}
    assert body["dialling"]["dial_anywhere"] is False
    assert gone.status_code == 204
    assert nobody.status_code == 404
    assert busy.status_code == 409
    assert "live keys" in busy.json()["detail"]


@postgres
async def test_quotas_and_dial_guards_are_replaced_whole_per_world(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    org = knocking.org.id
    async with knocking.http(THE_OPS_KEY) as operator:
        quotas = await operator.put(
            f"{ORGS}/{org}/quotas",
            json={
                "env": "sandbox",
                "quotas": {"limits": {"minutes": 30, "seats": 2}, "lends": ["acme"]},
            },
        )
        again = await operator.put(
            f"{ORGS}/{org}/quotas", json={"env": "sandbox", "quotas": {"limits": {"seats": 3}}}
        )
        guards = await operator.put(f"{ORGS}/{org}/dialling", json={"per_minute": 2})
        read = await operator.get(f"{ORGS}/{org}")
    assert quotas.status_code == 200
    assert (quotas.json()["limits"]["minutes"], quotas.json()["lends"]) == (30, ["acme"])
    assert (again.json()["limits"]["minutes"], again.json()["limits"]["seats"]) == (None, 3)
    assert (guards.json()["per_minute"], guards.json()["dial_anywhere"]) == (2, False)
    assert read.json()["quotas"]["sandbox"]["limits"]["seats"] == 3
    assert read.json()["quotas"]["production"]["limits"]["seats"] is None


@postgres
async def test_an_agent_registered_in_the_wrong_org_moves_with_its_logs_and_numbers(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    pool = knocking.gateway.connections.pool
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    number = Route(org=knocking.org.id, agent=AGENT, channel="phone", number="+59829001199")
    await routes.put(pool, number, RouteWrite("typed"))
    async with knocking.http(THE_OPS_KEY) as operator:
        made = await operator.post(ORGS, json={"slug": "tienda"})
        target = made.json()["id"]
        moved = await operator.put(f"{ORGS}/tienda/agents", json={"agent": AGENT})
        nobody = await operator.put(f"{ORGS}/tienda/agents", json={"agent": "ghost"})
    assert moved.status_code == 200
    assert (moved.json()["org"], moved.json()["numbers"], moved.json()["stayed"]) == (
        "tienda",
        ["+59829001199"],
        [],
    )
    assert moved.json()["logs"] >= 1
    assert [route.org for route in await routes.of_org(pool, target, "production")] == [target]
    assert nobody.status_code == 404


@postgres
async def test_an_agent_that_is_not_the_orgs_is_erased_with_its_numbers_and_a_trail_row(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    pool = knocking.gateway.connections.pool
    number = Route(org=knocking.org.id, agent="intruso", channel="phone", number="+59829001199")
    await routes.put(pool, number, RouteWrite("typed"))
    async with knocking.http(THE_OPS_KEY) as operator:
        erased = await operator.delete(f"{ORGS}/{knocking.org.slug}/agents/intruso")
    assert erased.status_code == 200
    assert (erased.json()["what"], erased.json()["subject"]) == ("agent", "intruso")
    assert await routes.of_org(pool, knocking.org.id, "production") == []


@postgres
async def test_the_operator_seats_an_orgs_first_admin_and_makes_or_unmakes_an_operator(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    org = knocking.org.id
    async with knocking.http(THE_OPS_KEY) as operator:
        invited = await operator.post(
            f"{ORGS}/{org}/members",
            json={"email": "root@clinica.test", "name": "Root", "role": "admin"},
        )
        member = invited.json()["member"]["id"]
        runs = await operator.put(
            f"{ORGS}/{org}/members/{member}/operator", json={"operator": True}
        )
        stops = await operator.put(
            f"{ORGS}/{org}/members/{member}/operator", json={"operator": False}
        )
        listed = await operator.get(f"{ORGS}/{org}/members")
        removed = await operator.delete(f"{ORGS}/{org}/members/{member}")
        nobody = await operator.delete(f"{ORGS}/{org}/members/{member}")
    assert invited.status_code == 201
    assert invited.json()["link"].endswith(f"/invitations/{invited.json()['token']}")
    assert runs.json()["operator"] is True
    assert stops.json()["operator"] is False
    assert [row["email"] for row in listed.json()["members"]] == ["root@clinica.test"]
    assert listed.json()["seated"] == 1
    assert removed.status_code == 204
    assert nobody.status_code == 404


@postgres
async def test_the_break_glass_lets_a_password_open_an_org_whose_provider_is_down(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    connections = knocking.gateway.connections
    org = knocking.org.id
    async with knocking.http(THE_OPS_KEY) as operator:
        none = await operator.get(f"{ORGS}/{org}/sso")
        refused = await operator.put(f"{ORGS}/{org}/sso/required", json={"required": False})
    wired = OrgSso(
        org=org,
        client=Client("https://idp.test", "the-client", "shh"),
        domains=("clinica.test",),
        required=True,
    )
    await sso.put_sso(connections.pool, connections.vault, wired)
    async with knocking.http(THE_OPS_KEY) as operator:
        opened = await operator.put(f"{ORGS}/{org}/sso/required", json={"required": False})
    kept = await sso.sso_of(connections.pool, connections.vault, org)
    assert none.json()["configured"] is False
    assert refused.status_code == 404
    assert opened.json()["required"] is False
    assert kept is not None
    assert kept.required is False
    assert "shh" not in opened.text


@postgres
async def test_the_operator_mints_lists_and_revokes_an_orgs_keys(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    org = knocking.org.id
    async with knocking.http(THE_OPS_KEY) as operator:
        minted = await operator.post(
            f"{ORGS}/{org}/keys", json={"env": "sandbox", "label": "ci", "scopes": ["calls"]}
        )
        listed = await operator.get(f"{ORGS}/{org}/keys")
        mine = [row for row in listed.json() if row["label"] == "ci"]
        revoked = await operator.post(f"/v1/ops/keys/{mine[0]['fingerprint']}/revoke")
        again = await operator.post(f"/v1/ops/keys/{mine[0]['fingerprint']}/revoke")
        bad = await operator.post(f"{ORGS}/{org}/keys", json={"env": "sandbox", "scopes": ["x"]})
    assert minted.status_code == 200
    assert minted.json()["key"].startswith("pc_test_")
    assert minted.json()["scopes"] == ["calls"]
    assert [row["kind"] for row in mine] == ["server"]
    assert revoked.json()["revoked"] is True
    assert again.status_code == 404
    assert bad.status_code == 400


@postgres
async def test_the_operator_sets_and_takes_back_an_orgs_own_vendor_key(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    org = knocking.org.id
    async with knocking.http(THE_OPS_KEY) as operator:
        put = await operator.put(f"{ORGS}/{org}/provider-keys/{ACME}", json={"key": "theirs"})
        listed = await operator.get(f"{ORGS}/{org}/provider-keys")
        gone = await operator.delete(f"{ORGS}/{org}/provider-keys/{ACME}")
        again = await operator.delete(f"{ORGS}/{org}/provider-keys/{ACME}")
    assert put.status_code == 204
    assert listed.json() == {"vendors": [ACME]}
    assert gone.status_code == 204
    assert again.status_code == 404


@postgres
async def test_forgetting_an_org_erases_its_calls_too_and_leaves_the_trail(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    store = knocking.gateway.logs.store
    async with knocking.http(THE_OPS_KEY) as operator:
        made = await operator.post(ORGS, json={"slug": "tienda", "name": "La Tienda"})
        org = made.json()["id"]
        call = await logged_call(store, org)
        gone = await operator.delete(f"{ORGS}/tienda")
    assert gone.status_code == 204
    assert await store.whole(call) == []
    trail = await erasure.trail(knocking.gateway.connections.pool, org)
    assert [(row.what, row.calls, row.asked_by) for row in trail] == [("org", 1, "operator")]


@postgres
async def test_a_traceback_finds_a_call_whose_org_was_erased_by_its_record(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    store = knocking.gateway.logs.store
    async with knocking.http(THE_OPS_KEY) as operator:
        made = await operator.post(ORGS, json={"slug": "tienda", "name": "La Tienda"})
        call = await logged_call(store, made.json()["id"], ACall(caller="+34600777888"))
        await operator.delete(f"{ORGS}/tienda")
        # The store's clock starts calls in 1970, before the default 24 months.
        query = {"number": "+34600777888", "since": 0}
        found = await operator.get("/v1/ops/traceback", params=query)
        refused = await operator.get("/v1/ops/traceback", params={"number": "tomorrow"})
    assert found.status_code == 200
    assert [(row["call"], row["erased"]) for row in found.json()["calls"]] == [(call, True)]
    assert refused.status_code == 400


@postgres
async def test_the_operator_reads_every_orgs_time_served_and_nobody_else_does(
    knocking: Knocking,
) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        answer = await operator.get("/v1/ops/hosted-usage", params={"month": "2026-09"})
    async with knocking.http(knocking.app["production"]) as http:
        refused = await http.get("/v1/ops/hosted-usage")
    assert answer.json() == {"since": "2026-09-01", "until": "2026-10-01", "rows": []}
    assert refused.status_code in (401, 403)
