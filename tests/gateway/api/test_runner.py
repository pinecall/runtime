"""Tests for the runner's doors: the apps it is to run, what starts each, and its reports."""

import httpx

from pinecall.domain.names import JsonObject
from pinecall.domain.person import THE_RUNNER
from pinecall.tenancy import keys, orgs
from tests.conftest import BOX_DOMAIN, Knocking, issued, postgres, received_until, sent
from tests.tenancy.test_hosting import PROJECT

HEARTBEAT = "/v1/runner/heartbeat"


MADE_UP = "made-up-by-this-test"


async def a_runner(knocking: Knocking) -> str:
    """The production runner's key, in the box's own org."""
    pool = knocking.gateway.connections.pool
    return await issued(pool, "default", "production", frozenset({THE_RUNNER}))


async def uploaded(knocking: Knocking, name: str = "support") -> JsonObject:
    async with knocking.http(knocking.app["production"]) as http:
        answer = await http.post(f"/v1/hosted/{name}/releases", content=PROJECT)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def beat(knocking: Knocking, runner: str, *reports: JsonObject) -> httpx.Response:
    async with knocking.http(runner) as http:
        return await http.post(HEARTBEAT, json={"runner": "apps-1", "reports": list(reports)})


@postgres
async def test_a_world_that_hosts_nothing_wants_nothing_running(knocking: Knocking) -> None:
    answer = await beat(knocking, await a_runner(knocking))
    assert (answer.status_code, answer.json()) == (200, {"world": "production", "apps": []})


@postgres
async def test_the_runner_is_told_every_orgs_apps_in_its_world_with_the_host_to_run_each(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    other = await orgs.create(pool, "tienda", "Tienda")
    await uploaded(knocking)
    theirs = await issued(pool, other.id, "production", frozenset({"app"}))
    async with knocking.http(theirs) as http:
        await http.post("/v1/hosted/billing/releases", content=PROJECT)
    wanted = (await beat(knocking, await a_runner(knocking))).json()["apps"]
    assert {(app["org"], app["name"], app["release"]) for app in wanted} == {
        (knocking.org.id, "support", 1),
        (other.id, "billing", 1),
    }
    [support] = [app for app in wanted if app["name"] == "support"]
    assert support["host"].startswith("support-r1-")
    assert (support["registered"], support["failed"], len(support["sha256"])) == (False, False, 64)


@postgres
async def test_what_the_sandbox_hosts_the_production_runner_is_not_told(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        await http.post("/v1/hosted/support/releases", content=PROJECT)
    assert (await beat(knocking, await a_runner(knocking))).json()["apps"] == []


@postgres
async def test_an_app_is_registered_once_a_socket_of_its_org_says_it_runs_on_that_host(
    knocking: Knocking,
) -> None:
    runner = await a_runner(knocking)
    await uploaded(knocking)
    [wanted] = (await beat(knocking, runner)).json()["apps"]
    socket = await knocking.socket("/v1/apps", knocking.app["production"])
    await sent(socket, "agent.register", {"routes": [], "host": wanted["host"]})
    await received_until(socket, "agent.registered")
    [after] = (await beat(knocking, runner)).json()["apps"]
    await socket.close()
    assert (wanted["registered"], after["registered"]) == (False, True)


@postgres
async def test_a_report_of_the_wanted_host_is_kept_and_the_org_reads_it(
    knocking: Knocking,
) -> None:
    runner = await a_runner(knocking)
    await uploaded(knocking)
    [wanted] = (await beat(knocking, runner)).json()["apps"]
    report = {"org": wanted["org"], "name": "support", "host": wanted["host"]}
    failed = await beat(knocking, runner, {**report, "state": "failed", "why": "npm exited 1"})
    async with knocking.http(knocking.app["production"]) as http:
        [broken] = (await http.get("/v1/hosted")).json()["apps"]
    await beat(knocking, runner, {**report, "state": "live"})
    async with knocking.http(knocking.app["production"]) as http:
        [serving] = (await http.get("/v1/hosted")).json()["apps"]
    assert failed.json()["apps"][0]["failed"] is True
    assert (broken["failed_why"], broken["live_release"]) == ("npm exited 1", None)
    assert serving["live_release"] == 1


@postgres
async def test_a_report_of_a_host_no_longer_wanted_changes_nothing(knocking: Knocking) -> None:
    runner = await a_runner(knocking)
    await uploaded(knocking)
    [first] = (await beat(knocking, runner)).json()["apps"]
    await uploaded(knocking)
    stale = {"org": first["org"], "name": "support", "host": first["host"], "state": "failed"}
    [after] = (await beat(knocking, runner, stale)).json()["apps"]
    assert (after["release"], after["failed"]) == (2, False)


@postgres
async def test_the_environment_is_the_orgs_secrets_the_apps_token_and_the_worlds_address(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    runner = await a_runner(knocking)
    await uploaded(knocking)
    async with knocking.http(knocking.app["production"]) as http:
        await http.put("/v1/secrets/CRM_TOKEN", json={"value": MADE_UP})
    async with knocking.http(runner) as http:
        answer = await http.get(f"/v1/runner/apps/{knocking.org.id}/support/environment")
        nobody = await http.get(f"/v1/runner/apps/{knocking.org.id}/nobody/environment")
    environment = answer.json()["environment"]
    assert set(environment) == {"CRM_TOKEN", "PINECALL_KEY", "PINECALL_URL"}
    assert environment["CRM_TOKEN"] == MADE_UP
    assert environment["PINECALL_URL"] == f"https://{BOX_DOMAIN}"
    bearer = await keys.verify(pool, environment["PINECALL_KEY"])
    assert bearer is not None
    assert (bearer.key.org, bearer.key.env) == (knocking.org.id, "production")
    assert nobody.status_code == 404


@postgres
async def test_the_runner_reads_the_source_of_any_orgs_release(knocking: Knocking) -> None:
    runner = await a_runner(knocking)
    await uploaded(knocking)
    async with knocking.http(runner) as http:
        source = await http.get(f"/v1/runner/apps/{knocking.org.id}/support/releases/1/source")
        nobody = await http.get(f"/v1/runner/apps/{knocking.org.id}/support/releases/2/source")
    assert (source.status_code, source.content) == (200, PROJECT)
    assert nobody.status_code == 404


@postgres
async def test_no_key_of_an_org_opens_the_runners_doors_and_the_runners_opens_no_orgs(
    knocking: Knocking,
) -> None:
    runner = await a_runner(knocking)
    async with knocking.http(knocking.app["production"]) as http:
        answers = [
            await http.post(HEARTBEAT, json={"runner": "apps-1"}),
            await http.get(f"/v1/runner/apps/{knocking.org.id}/support/environment"),
            await http.get(f"/v1/runner/apps/{knocking.org.id}/support/releases/1/source"),
        ]
    async with knocking.http(runner) as http:
        theirs = await http.get("/v1/hosted")
    assert [answer.status_code for answer in answers] == [403, 403, 403]
    assert theirs.status_code == 403
