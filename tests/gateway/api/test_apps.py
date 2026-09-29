"""Tests for the app socket and the org's list of apps."""

import asyncio
import json
import time

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._deps import POLICY_VIOLATION
from pinecall.log.logs import started_entry
from pinecall.tenancy import consents
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
    received,
    received_until,
    sent,
)
from tests.gateway.api.conftest import A_NUMBER, THE_CALLER, a_call, an_app


@postgres
async def test_a_socket_with_no_key_is_closed_with_a_policy_violation(knocking: Knocking) -> None:
    socket = await knocking.socket("/v1/apps", "pc_test_nobody")
    await socket.wait_closed()
    assert socket.close_code == POLICY_VIOLATION


@postgres
async def test_a_register_is_answered_and_written_on_the_agents_log(knocking: Knocking) -> None:
    socket = await knocking.socket("/v1/apps", knocking.app["sandbox"])
    await sent(socket, "agent.register", {"routes": [], "sdk": "ts/1.0"})
    registered = await received(socket)
    assert registered.type == "agent.registered"
    assert registered.data["env"] == "sandbox"
    assert str(registered.data["app"]).startswith("app_")
    kept = await knocking.gateway.logs.store.whole(f"@{AGENT}")
    assert [kept_one.type for kept_one in kept] == []
    await socket.close()


@postgres
async def test_a_production_register_is_kept_on_the_agents_log(knocking: Knocking) -> None:
    socket = await an_app(knocking, knocking.app["production"])
    kept = await knocking.gateway.logs.store.whole(f"@{AGENT}")
    assert [kept_one.type for kept_one in kept] == ["agent.registered"]
    await socket.close()


@postgres
async def test_a_configure_says_which_fields_changed(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    await sent(socket, "agent.configure", {"config": {"language": "es", "record": True}})
    configured = await received(socket)
    assert configured.type == "agent.configured"
    assert configured.data["changed"] == ["language", "record"]
    await socket.close()


@postgres
async def test_a_configure_before_a_register_is_refused(knocking: Knocking) -> None:
    socket = await knocking.socket("/v1/apps", knocking.app["sandbox"])
    await sent(socket, "agent.configure", {"config": {"language": "es"}})
    refused = await received(socket)
    assert refused.type == "error"
    assert "register it first" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_class_that_searches_with_nothing_attached_is_refused_naming_the_verb(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    await sent(socket, "agent.configure", {"config": {"uses_knowledge": True}})
    refused = await received(socket)
    assert refused.type == "error"
    assert "pinecall docs attach" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_command_the_protocol_never_heard_of_is_named(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    await sent(socket, "agent.dance", {})
    refused = await received(socket)
    assert refused.data["code"] == "unknown_command"
    assert "agent.dance" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_frame_that_is_no_command_is_refused_without_a_log(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    await socket.send(json.dumps({"hello": "there"}))
    refused = await received(socket)
    assert (refused.type, refused.data["code"], refused.seq) == ("error", "bad_shape", 0)
    await socket.close()


@postgres
async def test_ping_is_answered_with_pong(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    await sent(socket, "ping", {})
    assert (await received(socket)).type == "pong"
    await socket.close()


@postgres
async def test_a_dial_on_the_socket_is_sent_to_the_door_that_places_calls(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    await sent(socket, "call.dial", {"to": "+59899000002"})
    refused = await received(socket)
    assert "POST /v1/agents/{slug}/dial" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_a_call_command_with_no_call_running_says_so(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    await sent(socket, "agent.say", {"text": "hola"}, call="call_gone")
    refused = await received(socket)
    assert refused.data["code"] == "no_session"
    await socket.close()


@postgres
async def test_a_stopped_app_hears_why_and_is_gone_from_the_list(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        app = (await tenant.get("/v1/apps")).json()["apps"][0]["app"]
        stopped = await tenant.post(f"/v1/apps/{app}/stop")
        options = await received(socket)
        await socket.wait_closed()
        left = (await tenant.get("/v1/apps")).json()["apps"]
    assert stopped.json() == {"app": app, "stopped": True}
    assert (options.type, options.data["code"]) == ("error", "stopped")
    assert left == []


@postgres
async def test_another_orgs_app_is_not_there_to_stop(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    async with knocking.http(knocking.app["production"]) as other_world:
        refused = await other_world.post("/v1/apps/app_whatever/stop")
    assert refused.status_code == 404
    await socket.close()


@postgres
async def test_the_same_slug_is_held_once_in_each_world_and_neither_sees_the_other(
    knocking: Knocking,
) -> None:
    sandbox = await an_app(knocking)
    production = await an_app(knocking, knocking.app["production"])
    await sent(sandbox, "agent.configure", {"config": {"language": "en-US"}})
    await received_until(sandbox, "agent.configured")
    async with knocking.http(knocking.app["production"]) as live:
        held_live = (await live.get("/v1/agents")).json()
        declared_live = (await live.get(f"/v1/agents/{AGENT}/config")).json()
    async with knocking.http(knocking.app["sandbox"]) as test:
        declared_test = (await test.get(f"/v1/agents/{AGENT}/config")).json()
    await sandbox.close()
    await asyncio.sleep(0.1)
    async with knocking.http(knocking.app["production"]) as live:
        still_held = (await live.get("/v1/agents")).json()
    assert [item["slug"] for item in held_live["agents"]] == [AGENT]
    assert declared_test["language"] == "en-US"
    assert declared_live["language"] != "en-US"
    assert [item["slug"] for item in still_held["agents"]] == [AGENT]
    assert knocking.gateway.sockets.slugs(knocking.org.id) == {AGENT}
    await production.close()


@postgres
async def test_an_answer_nobody_waits_for_is_told_so(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    answer: JsonObject = {"call_id": "c_nobody", "name": "book", "output": {}}
    await sent(socket, "tool.result", answer, call="call_nobody")
    refused = await received_until(socket, "error")
    assert refused.data["code"] == "no_session"
    assert "c_nobody" in str(refused.data["message"])
    await socket.close()


@postgres
async def test_the_tools_still_waiting_are_re_sent_to_the_socket_that_takes_the_call(
    knocking: Knocking,
) -> None:
    first = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        started = started_entry(context, A_NUMBER, time.time())
        await worker.post(
            f"/v1/calls/{context.call}/events", json={"type": "call.started", "data": started}
        )
        params = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {}},
            )
        )
        called = await received_until(first, "tool.call")
        await first.close()
        await asyncio.sleep(0.1)
        second = await an_app(knocking)
        attached = await received_until(second, "call.attached")
        again = await received_until(second, "tool.call")
        answer: JsonObject = {"call_id": "c1", "name": "book", "output": {"ok": True}}
        await sent(second, "tool.result", answer, call=context.call)
        result = await asyncio.wait_for(params, 5)
    assert attached.call == context.call
    assert again.seq == called.seq
    assert result.json()["output"] == {"ok": True}
    await second.close()


@postgres
async def test_an_opt_out_puts_the_calls_number_on_the_list_and_lands_nothing_in_the_log(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    await sent(app, "call.opt_out", {"note": "please stop calling"}, call=context.call)
    # Commands are taken in order: the pong comes back after the opt-out was written.
    await sent(app, "ping", {})
    await received_until(app, "pong")
    world = Scope(knocking.org.id, "sandbox")
    history = await consents.history(knocking.gateway.connections.pool, world, THE_CALLER)
    assert history.standing == "opted_out"
    assert (history.rows[0].given_by, history.rows[0].call) == (f"agent:{AGENT}", context.call)
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert "call.opt_out" not in kinds
    await sent(app, "call.opt_out", {}, call="call_gone")
    assert (await received(app)).data["code"] == "no_session"
    typed = a_call(knocking, channel="web")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=typed).written())
    await sent(app, "call.opt_out", {}, call=typed.call)
    refused = await received_until(app, "error")
    assert refused.data["code"] == "bad_shape"
    assert "no phone number" in str(refused.data["message"])
    await app.close()
