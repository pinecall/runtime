"""Tests for what a worker asks about an agent, and what the org holds."""

from pydantic import TypeAdapter

from pinecall.providers import catalog
from pinecall.providers.catalog import Stage
from pinecall.providers.credentials import Pipeline
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import (
    AGENT,
    Knocking,
    configured,
    issued,
    postgres,
    received,
    sent,
)
from tests.fakes.acme import ACME
from tests.gateway.api.conftest import A_NUMBER, a_call, an_app


@postgres
async def test_a_key_that_holds_no_agent_is_told_what_it_opens(knocking: Knocking) -> None:
    reads = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    socket = await knocking.socket("/v1/apps", reads)
    await socket.wait_closed()
    assert "does not open app" in (socket.close_reason or "")


@postgres
async def test_the_declaration_comes_back_whole_and_another_orgs_is_a_404(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    await sent(socket, "agent.configure", {"config": {"language": "es"}})
    await received(socket)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        found = await tenant.get(f"/v1/agents/{AGENT}/config")
        nobody = await tenant.get("/v1/agents/nobody/config")
    assert found.json()["language"] == "es"
    assert nobody.status_code == 404
    await socket.close()


@postgres
async def test_the_fleet_is_handed_the_stages_with_the_key_each_runs_on(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    scope = {"org": knocking.org.id, "env": "sandbox", "holder": ""}
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        stages = (await worker.get(f"/v1/agents/{AGENT}/provider-keys", params=scope)).json()
        without = await worker.get(f"/v1/agents/{AGENT}/provider-keys")
    assert stages["llm"]["vendor"] == "acme"
    assert stages["llm"]["credentials"] == "a key of the box"
    assert stages["tts"]["lent"] is True
    assert without.status_code == 400
    await socket.close()


@postgres
async def test_the_fleet_is_handed_each_fallback_on_its_key_and_reads_it_back(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    row = configured()
    backed = {
        **row.defaults,
        "llm": Stage(vendor=ACME, model="acme-1", fallbacks=(Stage(vendor=ACME, model="acme-2"),)),
    }
    await catalog.configure(
        knocking.gateway.connections.pool, row.model_copy(update={"defaults": backed})
    )
    scope = {"org": knocking.org.id, "env": "sandbox", "holder": ""}
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        stages = (await worker.get(f"/v1/agents/{AGENT}/provider-keys", params=scope)).json()
    read = TypeAdapter(Pipeline).validate_python(stages)
    (backup,) = read.llm.fallbacks
    assert (backup.model, backup.credentials, backup.lent) == ("acme-2", "a key of the box", True)
    assert read.stt.fallbacks == read.tts.fallbacks == ()
    await socket.close()


@postgres
async def test_a_key_that_only_reads_is_handed_no_keys(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    reads = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reads) as reader:
        refused = await reader.get(f"/v1/agents/{AGENT}/provider-keys")
    assert refused.status_code == 403
    await socket.close()


@postgres
async def test_the_fleet_finds_a_number_across_orgs_in_its_own_world_only(
    knocking: Knocking,
) -> None:
    async with knocking.gateway.connections.pool.connection() as connection:
        await connection.execute(
            "insert into routes (org, number, agent, channel, env) "
            "values (%s, %s, %s, 'phone', 'production')",
            (knocking.org.id, A_NUMBER, AGENT),
        )
    params = {"number": A_NUMBER, "channel": "phone"}
    async with knocking.http(knocking.fleet["production"]) as production:
        found = (await production.get("/v1/routes", params=params)).json()
    async with knocking.http(knocking.fleet["sandbox"]) as sandbox:
        elsewhere = (await sandbox.get("/v1/routes", params=params)).json()
    assert [item["agent"] for item in found] == [AGENT]
    assert elsewhere == []


@postgres
async def test_the_org_lists_its_agents_each_on_the_widget(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/agents")).json()
    assert listed == {"agents": [{"slug": AGENT, "channels": ["web"], "holder": None}]}
    await socket.close()


# A worker that names a call is answered in the call's scope, as its head keeps it, and never in
# one the query string claims instead.
@postgres
async def test_a_worker_naming_a_call_acts_in_the_calls_scope_and_no_other(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    context = a_call(knocking)
    ours = {"org": knocking.org.id, "env": "sandbox", "holder": ""}
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        by_the_call = await worker.get(f"/v1/agents/{AGENT}/config", params={"call": context.call})
        agreeing = await worker.get(
            f"/v1/agents/{AGENT}/config", params={**ours, "call": context.call}
        )
        elsewhere = await worker.get(
            f"/v1/agents/{AGENT}/provider-keys",
            params={**ours, "org": "org_other", "call": context.call},
        )
        unopened = await worker.get(
            f"/v1/agents/{AGENT}/provider-keys", params={**ours, "call": "call_nobody"}
        )
    assert (by_the_call.status_code, agreeing.status_code) == (200, 200)
    assert elsewhere.status_code == 404
    assert "not in the scope the dispatch names" in elsewhere.json()["detail"]
    assert unopened.status_code == 404
    assert "no call call_nobody was opened" in unopened.json()["detail"]
    await socket.close()


# Before its call is opened a worker names it as for_call, which picks its version and no scope.
@postgres
async def test_a_worker_asking_for_a_call_not_opened_yet_is_answered_its_canarys_version(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    settings = f"/v1/agents/{AGENT}/settings"
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(settings, json={"config": {"greeting": {"say": "Hola"}}})
        await org.put(settings, json={"config": {"greeting": {"say": "Buenas"}}, "if_version": 1})
        await org.put(f"{settings}/canary", json={"version": 2, "share": 100})
    ours = {"org": knocking.org.id, "env": "sandbox", "holder": ""}
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        picked = await worker.get(
            f"/v1/agents/{AGENT}/config", params={**ours, "for_call": "CA_unopened"}
        )
        unnamed = await worker.get(f"/v1/agents/{AGENT}/config", params=ours)
        stages = await worker.get(
            f"/v1/agents/{AGENT}/provider-keys", params={**ours, "for_call": "CA_unopened"}
        )
    assert picked.json()["greeting"]["say"] == "Buenas"
    assert unnamed.json()["greeting"]["say"] == "Hola", "an older worker names no call: the rest"
    assert stages.status_code == 200
    await socket.close()
