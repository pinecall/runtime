"""Tests for an agent's doors and the app socket, knocked on a real gateway."""

import asyncio
import json
import time
from datetime import date

from websockets.asyncio.client import ClientConnection

from pinecall.domain.types import CallContext, JsonObject, Route
from pinecall.gateway.deps import POLICY_VIOLATION
from pinecall.log.log import started_entry
from pinecall.tenancy import keys
from pinecall.wire.rest import Heartbeat, Opening
from tests.conftest import (
    AGENT,
    Knocking,
    a_developer,
    issued,
    postgres,
    said,
    said_until,
    sent,
)

A_NUMBER = "+59829001199"
HER_PHONE = "+59899000001"


def a_call(knocking: Knocking) -> CallContext:
    """A call of the org's agent that rang at its number, in the sandbox."""
    route = Route(org=knocking.org.id, agent=AGENT, channel="phone", number=A_NUMBER, env="sandbox")
    return CallContext(
        call=f"call_{time.time_ns()}",
        channel="phone",
        direction="inbound",
        caller=HER_PHONE,
        route=route,
        today=date(2026, 9, 28),
    )


async def an_app(
    knocking: Knocking, key: str | None = None, *, console: bool = False
) -> ClientConnection:
    """An app socket holding the agent, in the key's world."""
    socket = await knocking.socket("/v1/apps", key or knocking.app["sandbox"])
    await sent(socket, "agent.register", {"routes": [], "takes_unclaimed": not console})
    await said_until(socket, "agent.registered")
    return socket


# ── the socket ──


@postgres
async def test_a_socket_with_no_key_is_closed_with_a_policy_violation(gateway: Knocking) -> None:
    socket = await gateway.socket("/v1/apps", "pc_test_nobody")
    await socket.wait_closed()
    assert socket.close_code == POLICY_VIOLATION


@postgres
async def test_a_key_that_holds_no_agent_is_told_what_it_opens(gateway: Knocking) -> None:
    reads = await issued(gateway.box.pool, gateway.org.id, "sandbox", frozenset({"calls"}))
    socket = await gateway.socket("/v1/apps", reads)
    await socket.wait_closed()
    assert "does not open app" in (socket.close_reason or "")


@postgres
async def test_a_register_is_answered_and_written_on_the_agents_log(gateway: Knocking) -> None:
    socket = await gateway.socket("/v1/apps", gateway.app["sandbox"])
    await sent(socket, "agent.register", {"routes": [], "sdk": "ts/1.0"})
    registered = await said(socket)
    assert registered.type == "agent.registered"
    assert registered.data["env"] == "sandbox"
    assert str(registered.data["app"]).startswith("app_")
    kept = await gateway.box.logs.store.whole(f"@{AGENT}")
    assert [one.type for one in kept] == []
    await socket.close()


@postgres
async def test_a_production_register_is_kept_on_the_agents_log(gateway: Knocking) -> None:
    socket = await an_app(gateway, gateway.app["production"])
    kept = await gateway.box.logs.store.whole(f"@{AGENT}")
    assert [one.type for one in kept] == ["agent.registered"]
    await socket.close()


@postgres
async def test_a_configure_says_which_fields_changed(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    await sent(socket, "agent.configure", {"config": {"language": "es", "record": True}})
    configured = await said(socket)
    assert configured.type == "agent.configured"
    assert configured.data["changed"] == ["language", "record"]
    await socket.close()


@postgres
async def test_a_configure_before_a_register_is_refused(gateway: Knocking) -> None:
    socket = await gateway.socket("/v1/apps", gateway.app["sandbox"])
    await sent(socket, "agent.configure", {"config": {"language": "es"}})
    refused = await said(socket)
    assert refused.type == "error"
    assert "register it first" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_class_that_searches_with_nothing_attached_is_refused_naming_the_verb(
    gateway: Knocking,
) -> None:
    socket = await an_app(gateway)
    await sent(socket, "agent.configure", {"config": {"uses_knowledge": True}})
    refused = await said(socket)
    assert refused.type == "error"
    assert "pinecall docs attach" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_command_the_protocol_never_heard_of_is_named(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    await sent(socket, "agent.dance", {})
    refused = await said(socket)
    assert refused.data["code"] == "unknown_command"
    assert "agent.dance" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_frame_that_is_no_command_is_refused_without_a_log(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    await socket.send(json.dumps({"hello": "there"}))
    refused = await said(socket)
    assert (refused.type, refused.data["code"], refused.seq) == ("error", "bad_shape", 0)
    await socket.close()


@postgres
async def test_ping_is_answered_with_pong(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    await sent(socket, "ping", {})
    assert (await said(socket)).type == "pong"
    await socket.close()


@postgres
async def test_a_dial_on_the_socket_is_sent_to_the_door_that_places_calls(
    gateway: Knocking,
) -> None:
    socket = await an_app(gateway)
    await sent(socket, "call.dial", {"to": "+59899000002"})
    refused = await said(socket)
    assert "POST /v1/agents/{slug}/dial" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_call_command_with_no_call_running_says_so(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    await sent(socket, "agent.say", {"text": "hola"}, call="call_gone")
    refused = await said(socket)
    assert refused.data["code"] == "no_session"
    await socket.close()


# ── what a worker asks ──


@postgres
async def test_the_declaration_comes_back_whole_and_another_orgs_is_a_404(
    gateway: Knocking,
) -> None:
    socket = await an_app(gateway)
    await sent(socket, "agent.configure", {"config": {"language": "es"}})
    await said(socket)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        found = await tenant.get(f"/v1/agents/{AGENT}/config")
        nobody = await tenant.get("/v1/agents/nobody/config")
    assert found.json()["language"] == "es"
    assert nobody.status_code == 404
    await socket.close()


@postgres
async def test_the_fleet_is_handed_the_stages_with_the_key_each_runs_on(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    corner = {"org": gateway.org.id, "env": "sandbox", "holder": ""}
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        stages = (await worker.get(f"/v1/agents/{AGENT}/provider-keys", params=corner)).json()
        without = await worker.get(f"/v1/agents/{AGENT}/provider-keys")
    assert stages["llm"]["vendor"] == "acme"
    assert stages["llm"]["credentials"] == "a key of the box"
    assert stages["tts"]["lent"] is True
    assert without.status_code == 400
    await socket.close()


@postgres
async def test_a_key_that_only_reads_is_handed_no_keys(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    reads = await issued(gateway.box.pool, gateway.org.id, "sandbox", frozenset({"calls"}))
    async with gateway.http(reads) as reader:
        refused = await reader.get(f"/v1/agents/{AGENT}/provider-keys")
    assert refused.status_code == 403
    await socket.close()


@postgres
async def test_the_fleet_finds_a_number_across_orgs_in_its_own_world_only(
    gateway: Knocking,
) -> None:
    async with gateway.box.pool.connection() as connection:
        await connection.execute(
            "insert into routes (org, number, agent, channel, env) "
            "values (%s, %s, %s, 'phone', 'production')",
            (gateway.org.id, A_NUMBER, AGENT),
        )
    asked = {"number": A_NUMBER, "channel": "phone"}
    async with gateway.http(gateway.fleet["production"]) as production:
        found = (await production.get("/v1/routes", params=asked)).json()
    async with gateway.http(gateway.fleet["sandbox"]) as sandbox:
        elsewhere = (await sandbox.get("/v1/routes", params=asked)).json()
    assert [one["agent"] for one in found] == [AGENT]
    assert elsewhere == []


# ── what the org holds ──


@postgres
async def test_the_org_lists_its_agents_each_on_the_widget(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/agents")).json()
    assert listed == {"agents": [{"slug": AGENT, "channels": ["web"], "holder": None}]}
    await socket.close()


@postgres
async def test_a_stopped_app_hears_why_and_is_gone_from_the_list(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        app = (await tenant.get("/v1/apps")).json()["apps"][0]["app"]
        stopped = await tenant.post(f"/v1/apps/{app}/stop")
        told = await said(socket)
        await socket.wait_closed()
        left = (await tenant.get("/v1/apps")).json()["apps"]
    assert stopped.json() == {"app": app, "stopped": True}
    assert (told.type, told.data["code"]) == ("error", "stopped")
    assert left == []


@postgres
async def test_another_orgs_app_is_not_there_to_stop(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    async with gateway.http(gateway.app["production"]) as other_world:
        refused = await other_world.post("/v1/apps/app_whatever/stop")
    assert refused.status_code == 404
    await socket.close()


# ── the line ──


@postgres
async def test_the_first_terminal_to_hold_the_agent_answers_its_ring(gateway: Knocking) -> None:
    ana, ana_key = await a_developer(gateway, "ana@clinica.test")
    _, ben_key = await a_developer(gateway, "ben@clinica.test")
    first = await an_app(gateway, ana_key)
    later = await an_app(gateway, ben_key)
    async with gateway.http(ben_key) as ben:
        line = (await ben.get(f"/v1/agents/{AGENT}/line")).json()
    assert line["held"] is True
    assert line["holding"]["holder"] == ana
    assert line["yours"] is False
    await first.close()
    await later.close()


@postgres
async def test_a_claim_takes_the_line_and_dropping_it_hands_it_back(gateway: Knocking) -> None:
    ana, ana_key = await a_developer(gateway, "ana@clinica.test")
    ben, ben_key = await a_developer(gateway, "ben@clinica.test")
    first = await an_app(gateway, ana_key)
    later = await an_app(gateway, ben_key)
    async with gateway.http(ben_key) as asking:
        taken = (await asking.post(f"/v1/agents/{AGENT}/line")).json()
        dropped = (await asking.delete(f"/v1/agents/{AGENT}/line")).json()
    assert (taken["holding"]["holder"], taken["yours"]) == (ben, True)
    assert dropped["holding"]["holder"] == ana
    await first.close()
    await later.close()


@postgres
async def test_a_developers_phone_on_the_production_number_reaches_their_copy(
    gateway: Knocking,
) -> None:
    ana, ana_key = await a_developer(gateway, "ana@clinica.test")
    copy = await an_app(gateway, ana_key)
    async with gateway.http(ana_key) as her:
        said_back = (await her.put("/v1/line/from", json={"number": HER_PHONE})).json()
    asked = {"org": gateway.org.id, "caller": HER_PHONE}
    stranger = {"org": gateway.org.id, "caller": "+59899999999"}
    async with gateway.http(gateway.fleet["production"]) as worker:
        handed = (await worker.get(f"/v1/agents/{AGENT}/rings-for", params=asked)).json()
        kept = (await worker.get(f"/v1/agents/{AGENT}/rings-for", params=stranger)).json()
    assert said_back == {"calling": [HER_PHONE]}
    assert handed == {"holder": ana, "fleet": "pinecall-sandbox"}
    assert kept == {"holder": None, "fleet": None}
    await copy.close()


@postgres
async def test_a_server_key_has_no_person_to_route_a_phone_to(gateway: Knocking) -> None:
    async with gateway.http(gateway.app["sandbox"]) as server:
        refused = await server.put("/v1/line/from", json={"number": HER_PHONE})
    assert refused.status_code == 403


# ── the console's asks ──


@postgres
async def test_a_dev_verb_travels_down_the_socket_and_the_answer_comes_back(
    gateway: Knocking,
) -> None:
    socket = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as console:
        asked = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
        )
        request = await said(socket)
        answer: JsonObject = {"id": request.data["id"], "result": {"chats": []}}
        await sent(socket, "dev.answer", answer)
        answered = await asyncio.wait_for(asked, 5)
    assert request.type == "dev.request"
    assert answered.json() == {"chats": []}
    await socket.close()


@postgres
async def test_the_apps_refusal_is_the_consoles_status_and_sentence(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as console:
        asked = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/knowledge/knowledge.roster", json={})
        )
        request = await said(socket)
        refusal: JsonObject = {"status": 422, "detail": "no base named x"}
        await sent(socket, "dev.answer", {"id": request.data["id"], "refused": refusal})
        answered = await asyncio.wait_for(asked, 5)
    assert (answered.status_code, answered.json()["detail"]) == (422, "no base named x")
    await socket.close()


@postgres
async def test_a_verb_not_of_the_family_is_refused_before_the_app_is_asked(
    gateway: Knocking,
) -> None:
    async with gateway.http(gateway.app["sandbox"]) as console:
        refused = await console.post(f"/v1/agents/{AGENT}/dev/chat/memory.eval", json={})
    assert refused.status_code == 404


@postgres
async def test_nobody_holding_is_404_and_a_console_alone_is_409(gateway: Knocking) -> None:
    async with gateway.http(gateway.app["sandbox"]) as console:
        nobody = await console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
        held = await an_app(gateway, console=True)
        only_a_console = await console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
    assert (nobody.status_code, only_a_console.status_code) == (404, 409)
    await held.close()


# ── the fleet ──


@postgres
async def test_a_heartbeat_reaches_the_roster_of_its_fleet(gateway: Knocking) -> None:
    beat = Heartbeat(
        fleet="pinecall-sandbox", worker="w1", active=1, max_jobs=4, load=0.2, draining=False
    )
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        standing = (await worker.post("/v1/fleet/heartbeat", json=beat.written())).json()
        totals = (await worker.get("/v1/fleet/standing")).json()
    assert standing == {"cordoned": False, "full": False}
    assert (totals["fleet"], totals["workers"], totals["free"]) == ("pinecall-sandbox", 1, 3)


@postgres
async def test_a_tenants_key_opens_no_fleet_door(gateway: Knocking) -> None:
    beat = Heartbeat(
        fleet="pinecall", worker="w1", active=0, max_jobs=None, load=0.1, draining=False
    )
    async with gateway.http(gateway.app["production"]) as tenant:
        refused = await tenant.post("/v1/fleet/heartbeat", json=beat.written())
    assert refused.status_code == 403


@postgres
async def test_a_callback_the_overflow_took_is_listed_for_the_org(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    wanted = {"agent": AGENT, "channel": "phone", "number": "+59899000002", "call": None}
    async with gateway.http(gateway.fleet["sandbox"]) as overflow:
        taken = await overflow.post("/v1/callbacks", json=wanted)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/callbacks")).json()
    assert taken.status_code == 204
    assert [one["number"] for one in listed["requests"]] == ["+59899000002"]
    await socket.close()


@postgres
async def test_judging_is_turned_off_for_the_org(gateway: Knocking) -> None:
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        before = (await tenant.get("/v1/org/judging")).json()
        after = (await tenant.put("/v1/org/judging", json={"on": False})).json()
    assert (before["on"], after["on"]) == (True, False)


def test_a_room_token_reads_as_its_call() -> None:
    signer = keys.Signer("APIkey", "a secret of thirty-two bytes or more")
    visitor = keys.Visitor(expires_at=time.time() + 60, identity="web_1")
    token = keys.room_token(signer, "call_1", "talk", visitor)
    visit = keys.read(signer, token)
    assert visit is not None
    assert visit.call == "call_1"


# ── two worlds ──


@postgres
async def test_the_same_slug_is_held_once_in_each_world_and_neither_sees_the_other(
    gateway: Knocking,
) -> None:
    sandbox = await an_app(gateway)
    production = await an_app(gateway, gateway.app["production"])
    await sent(sandbox, "agent.configure", {"config": {"language": "en-US"}})
    await said_until(sandbox, "agent.configured")
    async with gateway.http(gateway.app["production"]) as live:
        held_live = (await live.get("/v1/agents")).json()
        declared_live = (await live.get(f"/v1/agents/{AGENT}/config")).json()
    async with gateway.http(gateway.app["sandbox"]) as test:
        declared_test = (await test.get(f"/v1/agents/{AGENT}/config")).json()
    await sandbox.close()
    await asyncio.sleep(0.1)
    async with gateway.http(gateway.app["production"]) as live:
        still_held = (await live.get("/v1/agents")).json()
    assert [one["slug"] for one in held_live["agents"]] == [AGENT]
    assert declared_test["language"] == "en-US"
    assert declared_live["language"] != "en-US"
    assert [one["slug"] for one in still_held["agents"]] == [AGENT]
    assert gateway.box.registry.slugs(gateway.org.id) == {AGENT}
    await production.close()


# ── the socket, further ──


@postgres
async def test_an_answer_nobody_waits_for_is_told_so(gateway: Knocking) -> None:
    socket = await an_app(gateway)
    answer: JsonObject = {"call_id": "c_nobody", "name": "book", "output": {}}
    await sent(socket, "tool.result", answer, call="call_nobody")
    refused = await said_until(socket, "error")
    assert refused.data["code"] == "no_session"
    assert "c_nobody" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_the_tools_still_waiting_are_re_sent_to_the_socket_that_takes_the_call(
    gateway: Knocking,
) -> None:
    first = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        started = started_entry(context, A_NUMBER, time.time())
        await worker.post(
            f"/v1/calls/{context.call}/events", json={"type": "call.started", "data": started}
        )
        asked = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {}},
            )
        )
        called = await said_until(first, "tool.call")
        await first.close()
        await asyncio.sleep(0.1)
        second = await an_app(gateway)
        attached = await said_until(second, "call.attached")
        again = await said_until(second, "tool.call")
        answer: JsonObject = {"call_id": "c1", "name": "book", "output": {"ok": True}}
        await sent(second, "tool.result", answer, call=context.call)
        result = await asyncio.wait_for(asked, 5)
    assert attached.call == context.call
    assert again.seq == called.seq
    assert result.json()["output"] == {"ok": True}
    await second.close()
