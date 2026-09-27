"""Tests for a call's doors, knocked on a real gateway over a real database."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date

import httpx
import jwt
import pytest
from livekit.agents.llm import ChatMessage
from websockets.asyncio.client import ClientConnection

from pinecall.domain.settings import Settings
from pinecall.domain.types import KEY_SCOPES, CallContext, Env, JsonObject, Route, new_call_id
from pinecall.gateway.app import app as app_of_gateway
from pinecall.gateway.deps import POLICY_VIOLATION
from pinecall.providers import catalog
from pinecall.tenancy import keys, orgs
from pinecall.wire.rest import Heartbeat, Opening, Sealing
from tests.conftest import (
    AGENT,
    Knocking,
    configured,
    issued,
    postgres,
    said,
    said_until,
    sent,
)
from tests.fakes import AcmeLLM

A_NUMBER = "+59829001199"
THE_CALLER = "+59899123456"


def a_call(knocking: Knocking, *, env: Env = "sandbox", channel: str = "phone") -> CallContext:
    """A call of the org's agent that rang at its number."""
    return CallContext(
        call=new_call_id(),
        channel="phone" if channel == "phone" else "web",
        direction="inbound",
        caller=THE_CALLER,
        route=Route(
            org=knocking.org.id,
            agent=AGENT,
            channel="phone" if channel == "phone" else "web",
            number=A_NUMBER if channel == "phone" else None,
            env=env,
        ),
        today=date(2026, 9, 28),
    )


async def an_app(knocking: Knocking, env: Env = "sandbox") -> ClientConnection:
    """An app socket holding the agent in the world."""
    socket = await knocking.socket("/v1/apps", knocking.app[env])
    await sent(socket, "agent.register", {"routes": []})
    await said_until(socket, "agent.registered")
    return socket


@postgres
async def test_a_call_the_worker_opens_rings_on_the_socket_holding_its_agent(
    gateway: Knocking,
) -> None:
    app = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        opened = await worker.post(
            "/v1/calls", json=Opening(agent=AGENT, context=context).written()
        )
    assert opened.status_code == 200
    assert opened.json() == {"seconds_left": None, "minutes": None}
    ringing = await said(app)
    assert (ringing.type, ringing.call) == ("call.ringing", context.call)
    await app.close()


@postgres
async def test_an_entry_the_worker_writes_comes_back_numbered_and_reaches_the_app(
    gateway: Knocking,
) -> None:
    app = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        written = await worker.post(
            f"/v1/calls/{context.call}/events",
            json={"type": "custom", "data": {"name": "booked", "data": {}}},
        )
    assert written.status_code == 200
    assert written.json()["seq"] == 2
    assert (await said_until(app, "custom")).seq == 2
    await app.close()


@postgres
async def test_a_word_the_protocol_does_not_have_is_refused(gateway: Knocking) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        refused = await worker.post(
            f"/v1/calls/{context.call}/events", json={"type": "made.up", "data": {}}
        )
    assert refused.status_code == 400
    assert "made.up" in refused.json()["detail"]


@postgres
async def test_a_call_nobody_opened_here_is_the_same_404_as_another_orgs(
    gateway: Knocking,
) -> None:
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        refused = await worker.post(
            "/v1/calls/call_nobody/events", json={"type": "custom", "data": {}}
        )
    assert refused.status_code == 404
    assert "POST /v1/calls" in refused.json()["detail"]


@postgres
async def test_the_fleet_of_one_world_opens_no_call_of_the_other(gateway: Knocking) -> None:
    context = a_call(gateway, env="production")
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        refused = await worker.post(
            "/v1/calls", json=Opening(agent=AGENT, context=context).written()
        )
    assert refused.status_code == 403
    assert "wrong key" in refused.json()["detail"]


@postgres
async def test_a_tool_goes_to_the_app_as_tool_call_and_its_answer_comes_back(
    gateway: Knocking,
) -> None:
    app = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        asked = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {"day": "monday"}},
            )
        )
        called = await said_until(app, "tool.call")
        assert called.data["arguments"] == {"day": "monday"}
        answer: JsonObject = {"call_id": "c1", "name": "book", "output": {"booked": True}}
        await sent(app, "tool.result", answer, call=context.call)
        result = await asyncio.wait_for(asked, 5)
    assert result.json()["output"] == {"booked": True}
    assert (await said_until(app, "tool.result")).data["call_id"] == "c1"
    await app.close()


@postgres
async def test_an_apps_command_for_a_workers_call_is_held_for_the_worker(
    gateway: Knocking,
) -> None:
    app = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        await sent(app, "agent.say", {"text": "hola"}, call=context.call)
        async with worker.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            lines = stream.aiter_lines()
            data = await asyncio.wait_for(_first_data(lines), 5)
    assert json.loads(data)["type"] == "agent.say"
    await app.close()


async def _first_data(lines: AsyncIterator[str]) -> str:
    async for line in lines:
        if line.startswith("data:"):
            return line[5:].strip()
    return ""


@postgres
async def test_the_seal_prices_the_call_and_its_score_ends_the_log(gateway: Knocking) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        await worker.post(
            f"/v1/calls/{context.call}/events",
            json={
                "type": "call.ended",
                "data": {
                    "reason": "caller_hung_up",
                    "ended_by": "caller",
                    "ended_at": 10.0,
                    "duration_s": 42.0,
                },
            },
        )
        sealed = await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=Sealing(usage=[], outcome="booked", lent=["acme"]).written(),
        )
        again = await worker.post(
            f"/v1/calls/{context.call}/events", json={"type": "custom", "data": {}}
        )
    assert sealed.status_code == 204
    kinds = [entry.type for entry in await gateway.box.logs.store.whole(context.call)]
    assert kinds[-2:] == ["call.summary", "call.score"]
    assert await gateway.box.logs.store.sealed(context.call)
    assert again.status_code == 404
    async with gateway.box.pool.connection() as connection:
        row = await (
            await connection.execute("select lent from call_facts where call = %s", (context.call,))
        ).fetchone()
    assert row is not None
    assert row["lent"] == ["acme"]


async def a_route(knocking: Knocking, env: Env = "sandbox") -> None:
    """The agent answers at the number in the world."""
    async with knocking.box.pool.connection() as connection:
        await connection.execute(
            "insert into routes (org, number, agent, channel, env) "
            "values (%s, %s, %s, 'phone', %s)",
            (knocking.org.id, A_NUMBER, AGENT, env),
        )


@postgres
async def test_a_visitors_token_carries_the_dispatch_to_its_worlds_fleet(gateway: Knocking) -> None:
    app = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        minted = await tenant.post("/v1/tokens", json={"agent": AGENT, "contact": "c_42"})
    assert minted.status_code == 201
    said = minted.json()
    claims = jwt.decode(said["participant_token"], options={"verify_signature": False})
    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == "pinecall-sandbox"
    carried = json.loads(dispatch["metadata"])
    assert carried["org"] == gateway.org.id
    assert carried["env"] == "sandbox"
    assert carried["contact"] == "c_42"
    assert claims["video"]["room"] == said["call"]
    await app.close()


@postgres
@pytest.mark.parametrize("field", ["room_name", "participant_name", "participant_metadata"])
async def test_what_the_door_mints_is_refused_in_the_body(gateway: Knocking, field: str) -> None:
    app = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/tokens", json={"agent": AGENT, field: "x"})
    assert refused.status_code == 400
    assert field in refused.json()["detail"]
    await app.close()


@postgres
async def test_an_agent_nobody_holds_gets_no_token(gateway: Knocking) -> None:
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/tokens", json={"agent": AGENT})
    assert refused.status_code == 404


@postgres
async def test_a_full_fleet_refuses_the_token_and_the_agents_log_says_so(gateway: Knocking) -> None:
    app = await an_app(gateway)
    beat = Heartbeat(
        fleet="pinecall-sandbox", worker="w1", active=4, max_jobs=4, load=1.0, draining=False
    )
    gateway.box.roster.report(beat, time.time())
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/tokens", json={"agent": AGENT})
    assert refused.status_code == 503
    assert "every seat" in refused.json()["detail"]
    written = await gateway.box.logs.store.whole(f"@{AGENT}")
    assert (written[-1].type, written[-1].data["workers"]) == ("fleet.full", 1)
    await app.close()


@postgres
async def test_a_full_sandbox_leaves_production_open(gateway: Knocking) -> None:
    app = await an_app(gateway, "production")
    beat = Heartbeat(
        fleet="pinecall-sandbox", worker="w1", active=4, max_jobs=4, load=1.0, draining=False
    )
    gateway.box.roster.report(beat, time.time())
    async with gateway.http(gateway.app["production"]) as tenant:
        minted = await tenant.post("/v1/tokens", json={"agent": AGENT})
    assert minted.status_code == 201
    await app.close()


@postgres
async def test_a_code_is_claimed_by_the_call_that_keys_it_and_the_page_is_told(
    gateway: Knocking,
) -> None:
    await a_route(gateway)
    app = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        issued = (await tenant.post("/v1/codes", json={"agent": AGENT})).json()
    assert issued["number"] == A_NUMBER
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        keyed = await worker.post(f"/v1/calls/{context.call}/claim", json={"code": issued["code"]})
        nobodys = await worker.post(f"/v1/calls/{context.call}/claim", json={"code": "0000"})
    assert keyed.status_code == 204
    assert nobodys.status_code == 404
    async with gateway.http(issued["code_token"]) as page:
        standing = (await page.get(f"/v1/codes/{issued['code']}")).json()
    assert standing["status"] == "claimed"
    assert standing["call"] == context.call
    await app.close()


@postgres
async def test_a_code_needs_a_number_to_be_called_at(gateway: Knocking) -> None:
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/codes", json={"agent": AGENT})
    assert refused.status_code == 409


@postgres
async def test_a_text_call_is_answered_by_the_model_and_ends_sealed(gateway: Knocking) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola, soy la agenda"]]))
    app = await an_app(gateway)
    chat = await gateway.socket(f"/v1/chat?agent={AGENT}", gateway.app["sandbox"])
    started = await said_until(chat, "call.started")
    await chat.send(json.dumps({"text": "quiero un turno"}))
    answered = await said_until(chat, "turn.agent")
    assert answered.data["text"] == "hola, soy la agenda"
    await chat.close()
    call = started.call or ""
    for _ in range(50):
        if await gateway.box.logs.store.sealed(call):
            break
        await asyncio.sleep(0.1)
    kinds = [entry.type for entry in await gateway.box.logs.store.whole(call)]
    assert kinds[-3:] == ["call.ended", "call.summary", "call.score"]
    await app.close()


@postgres
async def test_a_text_call_to_an_agent_nobody_holds_is_closed_with_the_reason(
    gateway: Knocking,
) -> None:
    chat = await gateway.socket(f"/v1/chat?agent={AGENT}", gateway.app["sandbox"])
    await chat.wait_closed()
    assert chat.close_code == POLICY_VIOLATION
    assert "no app is holding" in (chat.close_reason or "")


@postgres
async def test_a_page_of_the_log_carries_the_entries_above_the_cursor(gateway: Knocking) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        for name in ("a", "b"):
            await worker.post(
                f"/v1/calls/{context.call}/events",
                json={"type": "custom", "data": {"name": name, "data": {}}},
            )
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        page = (await tenant.get(f"/v1/calls/{context.call}/events?after=1")).json()
        other = await tenant.get(f"/v1/calls/{context.call}/events", headers={"pinecall-env": "x"})
    assert [entry["seq"] for entry in page["entries"]] == [2, 3]
    assert page["next"] == 3
    assert page["live"] is True
    assert other.status_code == 403


@postgres
async def test_another_orgs_key_reads_nothing_of_the_call(gateway: Knocking) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
    stranger = await orgs.create(gateway.box.pool, "otra", "Otra")
    theirs = await issued(gateway.box.pool, stranger.id, "sandbox", KEY_SCOPES)
    async with gateway.http(theirs) as other:
        refused = await other.get(f"/v1/calls/{context.call}/events")
    assert refused.status_code == 404


@postgres
async def test_a_log_token_reads_its_own_call_and_no_other(gateway: Knocking) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
    token = keys.log_token(gateway.box.signer, context.call, "public")
    async with gateway.http(token) as page:
        own = await page.get(f"/v1/calls/{context.call}/state")
        another = await page.get("/v1/calls/call_other/state")
    assert own.status_code == 200
    assert own.json()["live"] is True
    assert another.status_code == 403


@postgres
async def test_the_org_lists_its_calls_newest_first(gateway: Knocking) -> None:
    calls = [a_call(gateway), a_call(gateway)]
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        for context in calls:
            await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/sessions")).json()
    assert [line["call"] for line in listed["calls"]] == [one.call for one in reversed(calls)]
    assert all(line["live"] for line in listed["calls"])


@postgres
async def test_a_gateway_that_forgot_a_call_serves_it_again_from_the_workers_word(
    gateway: Knocking,
) -> None:
    context = a_call(gateway)
    opening = Opening(agent=AGENT, context=context).written()
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=opening)
        gateway.box.live.close(context.call)
        gateway.box.logs.forget(context.call)
        again = await worker.post(f"/v1/calls/{context.call}/reopened", json=opening)
        written = await worker.post(
            f"/v1/calls/{context.call}/events",
            json={"type": "custom", "data": {"name": "x", "data": {}}},
        )
    assert again.status_code == 204
    assert written.status_code == 200


@postgres
async def test_a_call_nobody_opened_is_not_reopened_and_a_sealed_one_neither(
    gateway: Knocking,
) -> None:
    context = a_call(gateway)
    opening = Opening(agent=AGENT, context=context).written()
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        nobodys = await worker.post(f"/v1/calls/{context.call}/reopened", json=opening)
        await worker.post("/v1/calls", json=opening)
        await worker.post(
            f"/v1/calls/{context.call}/sealed", json=Sealing(usage=[], outcome="over").written()
        )
        over = await worker.post(f"/v1/calls/{context.call}/reopened", json=opening)
    assert nobodys.status_code == 404
    assert over.status_code == 409


# ── the desk ──


@postgres
async def test_the_orgs_key_lands_the_verb_on_the_workers_queue_naming_the_org(
    gateway: Knocking,
) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        async with gateway.http(gateway.app["sandbox"]) as desk:
            taken = await desk.post(
                f"/v1/calls/{context.call}/verbs", json={"verb": "say", "text": "un momento"}
            )
        async with worker.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            data = json.loads(await asyncio.wait_for(_first_data(stream.aiter_lines()), 5))
    assert taken.status_code == 202
    assert data["type"] == "supervisor.verb"
    assert data["data"]["by"] == {"id": f"key:{gateway.org.id}"}
    assert data["data"]["verb"] == {"verb": "say", "text": "un momento"}


@postgres
async def test_a_supervise_token_sends_the_verb_under_its_own_identity_and_reads_only_its_call(
    gateway: Knocking,
) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        visitor = keys.Visitor(expires_at=time.time() + 60, subject="mem_ana", name="Ana")
        token = keys.room_token(gateway.box.signer, context.call, "supervise", visitor)
        async with gateway.http(token) as desk:
            taken = await desk.post(f"/v1/calls/{context.call}/verbs", json={"verb": "takeover"})
            elsewhere = await desk.post("/v1/calls/call_other/verbs", json={"verb": "takeover"})
        async with worker.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            data = json.loads(await asyncio.wait_for(_first_data(stream.aiter_lines()), 5))
    assert taken.status_code == 202
    assert elsewhere.status_code == 403
    assert data["data"]["by"] == {"id": "mem_ana", "name": "Ana"}


@postgres
async def test_a_verb_needs_a_bearer_a_running_call_and_one_that_is_not_over(
    gateway: Knocking,
) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        async with httpx.AsyncClient(base_url=gateway.url) as nobody:
            unsigned = await nobody.post(
                f"/v1/calls/{context.call}/verbs", json={"verb": "takeover"}
            )
        async with gateway.http(gateway.app["sandbox"]) as desk:
            nowhere = await desk.post("/v1/calls/call_nobody/verbs", json={"verb": "takeover"})
        await worker.post(
            f"/v1/calls/{context.call}/sealed", json=Sealing(usage=[], outcome="over").written()
        )
        async with gateway.http(gateway.app["sandbox"]) as desk:
            over = await desk.post(f"/v1/calls/{context.call}/verbs", json={"verb": "takeover"})
    assert unsigned.status_code == 401
    assert nowhere.status_code == 404
    assert over.status_code == 409
    assert "read its log" in over.json()["detail"]


# ── the readers, further ──


async def a_logged_call(knocking: Knocking, *names: str) -> CallContext:
    """A call the worker opened with one custom entry per name, seq 2 onwards."""
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        for name in names:
            await worker.post(
                f"/v1/calls/{context.call}/events",
                json={"type": "custom", "data": {"name": name, "data": {}}},
            )
    return context


@postgres
async def test_a_key_in_the_query_string_is_refused_and_a_token_there_is_read(
    gateway: Knocking,
) -> None:
    context = await a_logged_call(gateway)
    token = keys.log_token(gateway.box.signer, context.call, "public")
    async with httpx.AsyncClient(base_url=gateway.url) as page:
        with_key = await page.get(f"/v1/calls/{context.call}/events?token={gateway.app['sandbox']}")
        with_token = await page.get(f"/v1/calls/{context.call}/events?token={token}")
    assert with_key.status_code == 401
    assert with_token.status_code == 200


@postgres
async def test_a_limit_stops_the_page_and_a_filtered_page_still_moves_the_cursor(
    gateway: Knocking,
) -> None:
    context = await a_logged_call(gateway, "a", "b", "c")
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        short = (await tenant.get(f"/v1/calls/{context.call}/events?limit=2")).json()
        nothing = (await tenant.get(f"/v1/calls/{context.call}/events?types=call.score")).json()
        refused = await tenant.get(f"/v1/calls/{context.call}/events?types=Made-Up")
    assert [one["seq"] for one in short["entries"]] == [1, 2]
    assert short["next"] == 2
    assert nothing["entries"] == []
    assert nothing["next"] == 4
    assert refused.status_code == 400


@postgres
async def test_last_event_id_resumes_above_the_cursor_and_the_higher_of_the_two_wins(
    gateway: Knocking,
) -> None:
    context = await a_logged_call(gateway, "a", "b", "c")
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        resumed = (
            await tenant.get(f"/v1/calls/{context.call}/events", headers={"Last-Event-ID": "3"})
        ).json()
        higher = (
            await tenant.get(
                f"/v1/calls/{context.call}/events?after=2", headers={"Last-Event-ID": "1"}
            )
        ).json()
    assert [one["seq"] for one in resumed["entries"]] == [4]
    assert [one["seq"] for one in higher["entries"]] == [3, 4]


@postgres
async def test_a_sealed_log_read_from_its_end_is_204_and_from_before_it_still_answers(
    gateway: Knocking,
) -> None:
    context = await a_logged_call(gateway, "a")
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post(
            f"/v1/calls/{context.call}/sealed", json=Sealing(usage=[], outcome="over").written()
        )
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        before = (await tenant.get(f"/v1/calls/{context.call}/events?after=1")).json()
        at_the_end = await tenant.get(f"/v1/calls/{context.call}/events?after={before['next']}")
    assert before["live"] is False
    assert [one["type"] for one in before["entries"]][-1] == "call.score"
    assert at_the_end.status_code == 204


@postgres
async def test_the_stream_carries_retry_then_the_log_and_ends_after_the_score(
    gateway: Knocking,
) -> None:
    context = await a_logged_call(gateway, "a")
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post(
            f"/v1/calls/{context.call}/sealed", json=Sealing(usage=[], outcome="over").written()
        )
    async with (
        gateway.http(gateway.app["sandbox"]) as tenant,
        tenant.stream(
            "GET", f"/v1/calls/{context.call}/events", headers={"Accept": "text/event-stream"}
        ) as stream,
    ):
        body = await asyncio.wait_for(stream.aread(), 5)
    lines = body.decode().splitlines()
    assert lines[0] == "retry: 1000"
    events = [line[7:] for line in lines if line.startswith("event: ")]
    assert events[0] == "call.ringing"
    assert events[-1] == "call.score"


@postgres
async def test_an_agents_own_log_is_read_by_its_org_and_never_ends(gateway: Knocking) -> None:
    app = await an_app(gateway, "production")
    async with gateway.http(gateway.app["production"]) as tenant:
        page = (await tenant.get(f"/v1/agents/{AGENT}/calls")).json()
    stranger = await orgs.create(gateway.box.pool, "otra", "Otra")
    theirs = await issued(gateway.box.pool, stranger.id, "production", KEY_SCOPES)
    async with gateway.http(theirs) as other:
        refused = await other.get(f"/v1/agents/{AGENT}/calls")
    assert [one["type"] for one in page["entries"]] == ["agent.registered"]
    assert page["live"] is True
    assert refused.status_code == 404
    await app.close()


@postgres
async def test_the_state_is_the_log_folded_and_a_call_nobody_wrote_is_a_404(
    gateway: Knocking,
) -> None:
    context = await a_logged_call(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        folded = (await tenant.get(f"/v1/calls/{context.call}/state")).json()
        nobodys = await tenant.get("/v1/calls/call_nobody/state")
    assert folded["last_seq"] == 1
    assert folded["state"]["status"] == "ringing"
    assert folded["live"] is True
    assert nobodys.status_code == 404


# ── a visitor, further ──


@postgres
async def test_the_ttl_is_a_minute_by_default_and_ten_at_most(gateway: Knocking) -> None:
    app = await an_app(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        short = (await tenant.post("/v1/tokens", json={"agent": AGENT})).json()
        long = (await tenant.post("/v1/tokens", json={"agent": AGENT, "ttl_s": 99_999})).json()
    lasts = [
        claims["exp"] - claims["nbf"]
        for claims in (
            jwt.decode(one["participant_token"], options={"verify_signature": False})
            for one in (short, long)
        )
    ]
    assert lasts[0] in (60, 61)
    assert lasts[1] in (600, 601)
    await app.close()


@postgres
async def test_a_chat_token_has_no_microphone_and_the_browser_is_told_the_public_url(
    gateway: Knocking,
) -> None:
    app = await an_app(gateway)
    public = Settings.model_validate(
        {**gateway.box.settings.variables, "LIVEKIT_PUBLIC_URL": "wss://sfu.example"}
    )
    app_of_gateway.state.wired = replace(gateway.box, settings=public)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        minted = (await tenant.post("/v1/tokens", json={"agent": AGENT, "scope": "chat"})).json()
    claims = jwt.decode(minted["participant_token"], options={"verify_signature": False})
    assert claims["video"]["canPublish"] is False
    assert minted["server_url"] == "wss://sfu.example"
    await app.close()


@postgres
async def test_an_expired_code_says_so_and_a_code_token_reads_nothing_but_its_code(
    gateway: Knocking,
) -> None:
    await a_route(gateway)
    async with gateway.http(gateway.app["sandbox"]) as tenant:
        issued_now = (await tenant.post("/v1/codes", json={"agent": AGENT, "ttl_s": 1})).json()
    await asyncio.sleep(1.1)
    async with gateway.http(issued_now["code_token"]) as page:
        asked = await page.get(f"/v1/codes/{issued_now['code']}")
        another = await page.get("/v1/codes/0000")
    assert asked.status_code == 200, asked.text
    assert asked.json()["status"] == "expired"
    assert another.status_code == 403


# ── a text call, further ──


@postgres
async def test_a_caller_back_on_a_forgotten_call_carries_on_and_nothing_starts_again(
    gateway: Knocking,
) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola"]]))
    app = await an_app(gateway)
    chat = await gateway.socket(f"/v1/chat?agent={AGENT}", gateway.app["sandbox"])
    started = await said_until(chat, "call.started")
    call = started.call or ""
    await chat.send(json.dumps({"text": "quiero un turno"}))
    await said_until(chat, "turn.agent")
    await chat.close(code=1012)
    await asyncio.sleep(0.2)
    gateway.box.live.close(call)
    gateway.box.logs.forget(call)
    back = await gateway.socket(f"/v1/chat?agent={AGENT}&call={call}", gateway.app["sandbox"])
    attached = await said_until(app, "call.attached")
    await back.send(json.dumps({"text": "sigo"}))
    await said_until(back, "turn.agent")
    session = gateway.box.live.calls[call].session
    assert session is not None
    (model,) = session.built
    assert isinstance(model, AcmeLLM)
    heard = [item.text_content for item in model.asked[-1].items if isinstance(item, ChatMessage)]
    await back.close()
    assert attached.call == call
    assert "quiero un turno" in heard
    kinds = [one.type for one in await gateway.box.logs.store.whole(call)]
    assert kinds.count("call.started") == 1
    assert "call.ended" not in kinds[: kinds.index("call.attached")]
    await app.close()


@postgres
async def test_a_call_that_is_over_is_not_taken_up(gateway: Knocking) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola"]]))
    app = await an_app(gateway)
    chat = await gateway.socket(f"/v1/chat?agent={AGENT}", gateway.app["sandbox"])
    started = await said_until(chat, "call.started")
    call = started.call or ""
    await chat.close()
    for _ in range(50):
        if await gateway.box.logs.store.sealed(call):
            break
        await asyncio.sleep(0.1)
    back = await gateway.socket(f"/v1/chat?agent={AGENT}&call={call}", gateway.app["sandbox"])
    await back.wait_closed()
    assert back.close_code == POLICY_VIOLATION
    assert "cannot be taken up" in (back.close_reason or "")
    await app.close()


# ── the worker's tools, further ──


@postgres
async def test_a_tool_nobody_answers_lapses_at_its_own_deadline_and_says_so(
    gateway: Knocking,
) -> None:
    app = await an_app(gateway)
    tool: JsonObject = {
        "name": "book",
        "description": "d",
        "parameters": {"type": "object"},
        "timeout_s": 0.3,
    }
    await sent(app, "agent.configure", {"config": {"tools": [tool]}})
    await said_until(app, "agent.configured")
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        declared = (
            await worker.get(f"/v1/agents/{AGENT}/config?org={gateway.org.id}&env=sandbox")
        ).json()
        assert declared["tools"][0]["timeout_s"] == 0.3, declared
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        lapsed = await worker.post(
            f"/v1/calls/{context.call}/tools?agent={AGENT}",
            json={"call_id": "c1", "name": "book", "arguments": {}},
        )
    assert lapsed.status_code == 200
    assert "did not answer within 0.3s" in lapsed.json()["error"]
    assert (await said_until(app, "tool.result")).data["call_id"] == "c1"
    await app.close()


@postgres
async def test_a_tool_of_an_agent_no_app_holds_is_a_refusal_and_not_a_wait(
    gateway: Knocking,
) -> None:
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        refused = await asyncio.wait_for(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {}},
            ),
            5,
        )
    assert refused.status_code == 409
    assert "no app is holding" in refused.json()["detail"]


@postgres
async def test_a_tool_that_returned_null_is_logged_with_its_null(gateway: Knocking) -> None:
    app = await an_app(gateway)
    context = a_call(gateway)
    async with gateway.http(gateway.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=Opening(agent=AGENT, context=context).written())
        asked = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {}},
            )
        )
        await said_until(app, "tool.call")
        await sent(
            app, "tool.result", {"call_id": "c1", "name": "book", "output": None}, call=context.call
        )
        result = await asyncio.wait_for(asked, 5)
    assert "output" in result.json()
    assert result.json()["output"] is None
    logged = await said_until(app, "tool.result")
    assert "output" in logged.data
    await app.close()


# ── the origins, further ──


@postgres
async def test_the_apps_preflight_is_answered_and_a_page_may_not_write(gateway: Knocking) -> None:
    async with httpx.AsyncClient(base_url=gateway.url) as browser:
        preflight = await browser.options(
            "/v1/agents",
            headers={
                "Origin": "capacitor://localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        writing = await browser.options(
            "/v1/calls/call_1/events",
            headers={"Origin": "https://shop.example", "Access-Control-Request-Method": "POST"},
        )
    assert preflight.status_code == 200
    assert "GET" in preflight.headers["access-control-allow-methods"]
    assert writing.status_code == 400
