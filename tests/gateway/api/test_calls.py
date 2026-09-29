"""Tests for the call doors: the worker's writes and the readers, on a real gateway."""

import asyncio
import json

import httpx
import pytest

from pinecall.domain.call import CallContext
from pinecall.domain.names import JsonObject
from pinecall.domain.person import KEY_SCOPES
from pinecall.tenancy import orgs, tokens
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import (
    AGENT,
    Knocking,
    issued,
    postgres,
    received,
    received_until,
    sent,
)
from tests.gateway.api.conftest import a_call, an_app, first_data


async def a_logged_call(knocking: Knocking, *names: str) -> CallContext:
    """A call the worker opened with one custom entry per name, seq 2 onwards."""
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        for name in names:
            await worker.post(
                f"/v1/calls/{context.call}/events",
                json={"type": "custom", "data": {"name": name, "data": {}}},
            )
    return context


@postgres
async def test_a_call_the_worker_opens_rings_on_the_socket_holding_its_agent(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        opened = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
    assert opened.status_code == 200
    assert opened.json() == {"seconds_left": None, "minutes": None}
    ringing = await received(app)
    assert (ringing.type, ringing.call) == ("call.ringing", context.call)
    await app.close()


@postgres
async def test_an_entry_the_worker_writes_comes_back_numbered_and_reaches_the_app(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        written = await worker.post(
            f"/v1/calls/{context.call}/events",
            json={"type": "custom", "data": {"name": "booked", "data": {}}},
        )
    assert written.status_code == 200
    assert written.json()["seq"] == 2
    assert (await received_until(app, "custom")).seq == 2
    await app.close()


@postgres
async def test_a_word_the_protocol_does_not_have_is_refused(knocking: Knocking) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        refused = await worker.post(
            f"/v1/calls/{context.call}/events", json={"type": "made.up", "data": {}}
        )
    assert refused.status_code == 400
    assert "made.up" in refused.json()["detail"]


@postgres
async def test_a_call_nobody_opened_here_is_the_same_404_as_another_orgs(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        refused = await worker.post(
            "/v1/calls/call_nobody/events", json={"type": "custom", "data": {}}
        )
    assert refused.status_code == 404
    assert "POST /v1/calls" in refused.json()["detail"]


@postgres
async def test_the_fleet_of_one_world_opens_no_call_of_the_other(knocking: Knocking) -> None:
    context = a_call(knocking, env="production")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        refused = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
    assert refused.status_code == 403
    assert "wrong key" in refused.json()["detail"]


@postgres
async def test_a_tool_goes_to_the_app_as_tool_call_and_its_answer_comes_back(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        params = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {"day": "monday"}},
            )
        )
        called = await received_until(app, "tool.call")
        assert called.data["arguments"] == {"day": "monday"}
        answer: JsonObject = {"call_id": "c1", "name": "book", "output": {"booked": True}}
        await sent(app, "tool.result", answer, call=context.call)
        result = await asyncio.wait_for(params, 5)
    assert result.json()["output"] == {"booked": True}
    assert (await received_until(app, "tool.result")).data["call_id"] == "c1"
    await app.close()


@postgres
async def test_an_apps_command_for_a_workers_call_is_held_for_the_worker(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        await sent(app, "agent.say", {"text": "hola"}, call=context.call)
        async with worker.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            lines = stream.aiter_lines()
            data = await asyncio.wait_for(first_data(lines), 5)
    assert json.loads(data)["type"] == "agent.say"
    await app.close()


@postgres
async def test_the_seal_prices_the_call_and_its_score_ends_the_log(knocking: Knocking) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
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
            json=SealCallRequest(usage=[], outcome="booked", lent=["acme"]).written(),
        )
        again = await worker.post(
            f"/v1/calls/{context.call}/events", json={"type": "custom", "data": {}}
        )
    assert sealed.status_code == 204
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert kinds[-2:] == ["call.summary", "call.score"]
    assert await knocking.gateway.logs.store.sealed(context.call)
    assert again.status_code == 404
    async with knocking.gateway.connections.pool.connection() as connection:
        row = await (
            await connection.execute("select lent from call_facts where call = %s", (context.call,))
        ).fetchone()
    assert row is not None
    assert row["lent"] == ["acme"]


@postgres
async def test_a_page_of_the_log_carries_the_entries_above_the_cursor(knocking: Knocking) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        for name in ("a", "b"):
            await worker.post(
                f"/v1/calls/{context.call}/events",
                json={"type": "custom", "data": {"name": name, "data": {}}},
            )
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        page = (await tenant.get(f"/v1/calls/{context.call}/events?after=1")).json()
        other = await tenant.get(f"/v1/calls/{context.call}/events", headers={"pinecall-env": "x"})
    assert [entry["seq"] for entry in page["entries"]] == [2, 3]
    assert page["next"] == 3
    assert page["live"] is True
    assert other.status_code == 403


@postgres
async def test_another_orgs_key_reads_nothing_of_the_call(knocking: Knocking) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    stranger = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    theirs = await issued(knocking.gateway.connections.pool, stranger.id, "sandbox", KEY_SCOPES)
    async with knocking.http(theirs) as other:
        refused = await other.get(f"/v1/calls/{context.call}/events")
    assert refused.status_code == 404


@postgres
async def test_a_call_nobody_wrote_yet_is_an_empty_live_page_not_a_404(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as reader:
        page = await reader.get("/v1/calls/call_minted_by_the_client/events")
    assert page.status_code == 200
    assert (page.json()["entries"], page.json()["live"]) == ([], True)


@postgres
async def test_a_log_token_reads_its_own_call_and_no_other(knocking: Knocking) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    token = tokens.log_token(knocking.gateway.signer, context.call, "public")
    async with knocking.http(token) as page:
        own = await page.get(f"/v1/calls/{context.call}/state")
        another = await page.get("/v1/calls/call_other/state")
    assert own.status_code == 200
    assert own.json()["live"] is True
    assert another.status_code == 403


@postgres
async def test_the_org_lists_its_calls_newest_first(knocking: Knocking) -> None:
    calls = [a_call(knocking), a_call(knocking)]
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        for context in calls:
            await worker.post(
                "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
            )
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/sessions")).json()
    assert [line["call"] for line in listed["calls"]] == [call.call for call in reversed(calls)]
    assert all(line["live"] for line in listed["calls"])


@postgres
async def test_a_gateway_that_forgot_a_call_serves_it_again_from_the_workers_word(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    opening = OpenCallRequest(agent=AGENT, context=context).written()
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=opening)
        knocking.gateway.live.close(context.call)
        knocking.gateway.logs.forget(context.call)
        again = await worker.post(f"/v1/calls/{context.call}/reopened", json=opening)
        written = await worker.post(
            f"/v1/calls/{context.call}/events",
            json={"type": "custom", "data": {"name": "x", "data": {}}},
        )
    assert again.status_code == 204
    assert written.status_code == 200


@postgres
async def test_a_call_nobody_opened_is_not_reopened_and_a_sealed_one_neither(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    opening = OpenCallRequest(agent=AGENT, context=context).written()
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        nobodys = await worker.post(f"/v1/calls/{context.call}/reopened", json=opening)
        await worker.post("/v1/calls", json=opening)
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="over").written(),
        )
        over = await worker.post(f"/v1/calls/{context.call}/reopened", json=opening)
    assert nobodys.status_code == 404
    assert over.status_code == 409


@postgres
async def test_a_key_in_the_query_string_is_refused_and_a_token_there_is_read(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking)
    token = tokens.log_token(knocking.gateway.signer, context.call, "public")
    async with httpx.AsyncClient(base_url=knocking.url) as page:
        with_key = await page.get(
            f"/v1/calls/{context.call}/events?token={knocking.app['sandbox']}"
        )
        with_token = await page.get(f"/v1/calls/{context.call}/events?token={token}")
    assert with_key.status_code == 401
    assert with_token.status_code == 200


@postgres
async def test_a_limit_stops_the_page_and_a_filtered_page_still_moves_the_cursor(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking, "a", "b", "c")
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        short = (await tenant.get(f"/v1/calls/{context.call}/events?limit=2")).json()
        nothing = (await tenant.get(f"/v1/calls/{context.call}/events?types=call.score")).json()
        refused = await tenant.get(f"/v1/calls/{context.call}/events?types=Made-Up")
    assert [item["seq"] for item in short["entries"]] == [1, 2]
    assert short["next"] == 2
    assert nothing["entries"] == []
    assert nothing["next"] == 4
    assert refused.status_code == 400


@postgres
async def test_last_event_id_resumes_above_the_cursor_and_the_higher_of_the_two_wins(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking, "a", "b", "c")
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        resumed = (
            await tenant.get(f"/v1/calls/{context.call}/events", headers={"Last-Event-ID": "3"})
        ).json()
        higher = (
            await tenant.get(
                f"/v1/calls/{context.call}/events?after=2", headers={"Last-Event-ID": "1"}
            )
        ).json()
    assert [item["seq"] for item in resumed["entries"]] == [4]
    assert [item["seq"] for item in higher["entries"]] == [3, 4]


@postgres
async def test_a_sealed_log_read_from_its_end_is_204_and_from_before_it_still_answers(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking, "a")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="over").written(),
        )
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        before = (await tenant.get(f"/v1/calls/{context.call}/events?after=1")).json()
        at_the_end = await tenant.get(f"/v1/calls/{context.call}/events?after={before['next']}")
    assert before["live"] is False
    assert [item["type"] for item in before["entries"]][-1] == "call.score"
    assert at_the_end.status_code == 204


@postgres
async def test_the_stream_carries_retry_then_the_log_and_ends_after_the_score(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking, "a")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="over").written(),
        )
    async with (
        knocking.http(knocking.app["sandbox"]) as tenant,
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
async def test_an_agents_own_log_is_read_by_its_org_and_never_ends(knocking: Knocking) -> None:
    app = await an_app(knocking, env="production")
    async with knocking.http(knocking.app["production"]) as tenant:
        page = (await tenant.get(f"/v1/agents/{AGENT}/calls")).json()
    stranger = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    theirs = await issued(knocking.gateway.connections.pool, stranger.id, "production", KEY_SCOPES)
    async with knocking.http(theirs) as other:
        refused = await other.get(f"/v1/agents/{AGENT}/calls")
    assert [item["type"] for item in page["entries"]] == ["agent.registered"]
    assert page["live"] is True
    assert refused.status_code == 404
    await app.close()


@postgres
async def test_the_state_is_the_log_folded_and_a_call_nobody_wrote_is_a_404(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        folded = (await tenant.get(f"/v1/calls/{context.call}/state")).json()
        nobodys = await tenant.get("/v1/calls/call_nobody/state")
    assert folded["last_seq"] == 1
    assert folded["state"]["status"] == "ringing"
    assert folded["live"] is True
    assert nobodys.status_code == 404


@postgres
async def test_a_tool_nobody_answers_lapses_at_its_own_deadline_and_says_so(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    tool: JsonObject = {
        "name": "book",
        "description": "d",
        "parameters": {"type": "object"},
        "timeout_s": 0.3,
    }
    await sent(app, "agent.configure", {"config": {"tools": [tool]}})
    await received_until(app, "agent.configured")
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        declared = (
            await worker.get(f"/v1/agents/{AGENT}/config?org={knocking.org.id}&env=sandbox")
        ).json()
        assert declared["tools"][0]["timeout_s"] == 0.3, declared
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        lapsed = await worker.post(
            f"/v1/calls/{context.call}/tools?agent={AGENT}",
            json={"call_id": "c1", "name": "book", "arguments": {}},
        )
    assert lapsed.status_code == 200
    assert "did not answer within 0.3s" in lapsed.json()["error"]
    assert (await received_until(app, "tool.result")).data["call_id"] == "c1"
    await app.close()


@postgres
async def test_a_tool_of_an_agent_no_app_holds_is_a_refusal_and_not_a_wait(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
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
async def test_a_tool_that_returned_null_is_logged_with_its_null(knocking: Knocking) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        params = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {}},
            )
        )
        await received_until(app, "tool.call")
        await sent(
            app, "tool.result", {"call_id": "c1", "name": "book", "output": None}, call=context.call
        )
        result = await asyncio.wait_for(params, 5)
    assert "output" in result.json()
    assert result.json()["output"] is None
    logged = await received_until(app, "tool.result")
    assert "output" in logged.data
    await app.close()


@postgres
async def test_the_apps_preflight_is_answered_and_a_page_may_not_write(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as browser:
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


@postgres
async def test_a_lookup_for_a_call_served_here_answers_the_tools_shape_and_its_time(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        searched = await worker.post(
            f"/v1/calls/{context.call}/lookup",
            json={"tool": "search", "input": {"query": "horarios"}, "speech_id": "sp_1"},
        )
        recalled = await worker.post(
            f"/v1/calls/{context.call}/lookup", json={"tool": "recall", "input": {"query": "x"}}
        )
        nobody = await worker.post(
            "/v1/calls/call_nobody/lookup", json={"tool": "search", "input": {"query": "x"}}
        )
    assert searched.status_code == 200
    assert searched.json()["output"] == {"chunks": []}
    assert isinstance(searched.json()["took_ms"], float)
    assert recalled.json()["output"] == {"facts": []}
    assert nobody.status_code == 404
    await app.close()


@postgres
async def test_remember_on_a_call_that_keeps_nothing_counts_no_op(knocking: Knocking) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        remembered = await worker.post(f"/v1/calls/{context.call}/remember")
        nobody = await worker.post("/v1/calls/call_nobody/remember")
    assert remembered.status_code == 200
    assert remembered.json()["ops"] == 0
    assert nobody.status_code == 404
    await app.close()


@postgres
async def test_the_orgs_feed_carries_its_own_worlds_calls_and_not_the_others(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with (
        knocking.http(knocking.app["production"]) as elsewhere,
        elsewhere.stream("GET", "/v1/events", headers={"Accept": "text/event-stream"}) as quiet,
        knocking.http(knocking.app["sandbox"]) as tenant,
        tenant.stream("GET", "/v1/events", headers={"Accept": "text/event-stream"}) as feed,
        knocking.http(knocking.fleet["sandbox"]) as worker,
    ):
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        data = await asyncio.wait_for(first_data(feed.aiter_lines()), 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(first_data(quiet.aiter_lines()), 0.3)
    assert json.loads(data)["type"] == "call.ringing"
