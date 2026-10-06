"""Tests for the call doors: the worker's writes and the readers, on a real gateway."""

import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest

from pinecall.channels import routes
from pinecall.channels.routes import RouteWrite
from pinecall.domain.call import CallContext, new_call_id
from pinecall.domain.names import JsonObject
from pinecall.domain.person import KEY_SCOPES
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from pinecall.tenancy import keys, orgs, policy, reads, tokens
from pinecall.wire.rest.accounts import OrgPolicy
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import (
    AGENT,
    Knocking,
    a_developer,
    issued,
    postgres,
    received,
    received_until,
    sent,
)
from tests.gateway.api.conftest import A_NUMBER, a_call, an_app, first_data


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
    assert opened.json() == {
        "seconds_left": None,
        "minutes": None,
        "disclosure": None,
        "recording_notice": "This call may be recorded.",
    }
    ringing = await received(app)
    assert (ringing.type, ringing.call) == ("call.ringing", context.call)
    await app.close()


@postgres
async def test_a_phone_call_the_worker_opens_marks_its_number_as_reached_once_a_minute(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    pool = knocking.gateway.connections.pool
    await routes.put(pool, context.route, RouteWrite("imported"))
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        first = await routes.record_of(pool, knocking.org.id, A_NUMBER)
        again = replace(context, call=new_call_id())
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=again).written())
    second = await routes.record_of(pool, knocking.org.id, A_NUMBER)
    assert first is not None
    assert second is not None
    assert first.last_call_at is not None
    assert second.last_call_at == first.last_call_at
    await app.close()


@postgres
async def test_an_outbound_call_opens_with_the_orgs_disclosure_and_its_notice_as_set(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    pool = knocking.gateway.connections.pool
    context = replace(a_call(knocking), direction="outbound")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        default = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
        await policy.put_policy(
            pool, knocking.org.id, OrgPolicy(disclosure="", recording_notice=False), by="m_1"
        )
        muted = replace(context, call=a_call(knocking).call)
        silent = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=muted).written()
        )
    assert default.json()["disclosure"] == (
        f"This is an automated assistant calling on behalf of {knocking.org.name}."
    )
    assert (silent.json()["disclosure"], silent.json()["recording_notice"]) == (None, None)
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
async def test_a_batch_comes_back_numbered_in_order_and_its_retry_with_the_same_seqs(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = a_call(knocking)
    batch: JsonObject = {
        "after": 0,
        "entries": [
            {"type": "custom", "data": {"name": "first", "data": {}}, "ts": 0.1},
            {"type": "user.transcript", "data": {"text": "ho"}, "ts": 0.2},
            {"type": "custom", "data": {"name": "second", "data": {}}, "ts": 0.3},
        ],
    }
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        written = await worker.post(f"/v1/calls/{context.call}/entries", json=batch)
        again = await worker.post(f"/v1/calls/{context.call}/entries", json=batch)
        ahead = await worker.post(f"/v1/calls/{context.call}/entries", json={**batch, "after": 9})
    assert (written.status_code, again.status_code, ahead.status_code) == (200, 200, 409)
    seqs = [entry["seq"] for entry in written.json()["entries"]]
    assert seqs == [entry["seq"] for entry in again.json()["entries"]] == [2, 3, 4]
    assert [entry["ephemeral"] for entry in written.json()["entries"]] == [False, True, False]
    assert [entry["ts"] for entry in written.json()["entries"]] == [0.1, 0.2, 0.3]
    assert (await received_until(app, "custom")).seq == 2
    await app.close()


@postgres
async def test_a_batch_is_refused_to_every_key_the_single_door_refuses(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    stranger = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    theirs = await issued(knocking.gateway.connections.pool, stranger.id, "sandbox", KEY_SCOPES)
    entry: JsonObject = {"type": "custom", "data": {"name": "n", "data": {}}}
    timed: JsonObject = {**entry, "ts": 1.0}
    for key in (knocking.fleet["production"], theirs):
        async with knocking.http(key) as other:
            single = await other.post(f"/v1/calls/{context.call}/events", json=entry)
            batch = await other.post(
                f"/v1/calls/{context.call}/entries", json={"after": 0, "entries": [timed]}
            )
        assert single.status_code in (403, 404)
        assert (batch.status_code, batch.json()) == (single.status_code, single.json())


@postgres
async def test_one_word_the_protocol_does_not_have_refuses_the_whole_batch(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    entries: list[JsonObject] = [
        {"type": "custom", "data": {"name": "n", "data": {}}, "ts": 1.0},
        {"type": "made.up", "data": {}, "ts": 1.0},
    ]
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        refused = await worker.post(
            f"/v1/calls/{context.call}/entries", json={"after": 0, "entries": entries}
        )
    async with knocking.http(knocking.app["sandbox"]) as reader:
        page = await reader.get(f"/v1/calls/{context.call}/events")
    assert refused.status_code == 400
    assert "made.up" in refused.json()["detail"]
    assert [entry["type"] for entry in page.json()["entries"]] == ["call.ringing"]


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
async def test_a_worker_reads_the_call_it_serves_with_the_fleets_key_alone(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        state = await worker.get(f"/v1/calls/{context.call}/state")
        page = await worker.get(f"/v1/calls/{context.call}/events")
    assert state.status_code == 200
    assert state.json()["last_seq"] == 1
    assert [entry["type"] for entry in page.json()["entries"]] == ["call.ringing"]


@postgres
async def test_the_fleets_key_reads_no_call_nobody_opened_where_a_tenant_reads_it_empty(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        state = await worker.get("/v1/calls/call_nobody/state")
        page = await worker.get("/v1/calls/call_nobody/events")
        recorded = await worker.get("/v1/calls/call_nobody/recording")
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        tailed = await tenant.get("/v1/calls/call_nobody/events")
    assert [state.status_code, page.status_code, recorded.status_code] == [404, 404, 404]
    assert "no call call_nobody was opened" in page.json()["detail"]
    assert (tailed.status_code, tailed.json()["entries"]) == (200, [])


# An agent's log, the org's floor and its lists name no call: a worker has none of them to read.
@postgres
async def test_the_fleets_key_reads_no_agent_log_no_floor_and_no_list(knocking: Knocking) -> None:
    await a_logged_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        refused = [
            await worker.get(path)
            for path in (f"/v1/agents/{AGENT}/calls", "/v1/events", "/v1/sessions")
        ]
    assert [answer.status_code for answer in refused] == [403, 403, 403]
    assert "does not open calls" in refused[0].json()["detail"]


@postgres
async def test_a_call_a_dial_claimed_is_opened_only_in_the_scope_its_head_keeps(
    knocking: Knocking,
) -> None:
    other = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    context = a_call(knocking)
    theirs = Claim(Scope(other.id, "sandbox"))
    await knocking.gateway.logs.store.claim(context.call, AGENT, other.id, theirs)
    opening = OpenCallRequest(agent=AGENT, context=context).written()
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        by_the_fleet = await worker.post("/v1/calls", json=opening)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        by_the_org = await tenant.post("/v1/calls", json=opening)
    assert (by_the_fleet.status_code, by_the_org.status_code) == (404, 404)
    assert context.call not in knocking.gateway.live.calls


@postgres
async def test_a_forgotten_call_is_served_again_only_in_the_scope_it_was_opened_in(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    elsewhere = replace(context, holder="m_somebody")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        knocking.gateway.live.close(context.call)
        knocking.gateway.logs.forget(context.call)
        moved = await worker.post(
            f"/v1/calls/{context.call}/reopened",
            json=OpenCallRequest(agent=AGENT, context=elsewhere).written(),
        )
    assert moved.status_code == 404
    assert context.call not in knocking.gateway.live.calls


@postgres
async def test_the_fleet_of_one_world_reads_no_call_of_the_other(knocking: Knocking) -> None:
    context = a_call(knocking, env="production")
    async with knocking.http(knocking.fleet["production"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    async with knocking.http(knocking.fleet["sandbox"]) as other:
        refused = await other.get(f"/v1/calls/{context.call}/events")
    assert refused.status_code == 404


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
    # Over, not forgotten: the worker is told so at once, as a reopen would have told it.
    assert again.status_code == 409
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


async def a_sealed_call(knocking: Knocking) -> CallContext:
    """A call the worker opened, ended and sealed."""
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="booked").written(),
        )
    return context


@postgres
async def test_an_ended_call_is_erased_and_reads_as_nobodys_afterwards(knocking: Knocking) -> None:
    context = await a_sealed_call(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        erased = await tenant.delete(f"/v1/calls/{context.call}")
        again = await tenant.delete(f"/v1/calls/{context.call}")
        state = await tenant.get(f"/v1/calls/{context.call}/state")
        trail = await tenant.get("/v1/org/erasures")
    assert erased.status_code == 200
    body = erased.json()
    assert (body["what"], body["subject"], body["env"], body["calls"]) == (
        "call",
        context.call,
        "sandbox",
        1,
    )
    assert body["entries"] > 0
    assert again.status_code == 404
    assert state.status_code == 404
    assert [row["subject"] for row in trail.json()["erasures"]] == [context.call]


@postgres
async def test_a_call_still_running_is_not_erased(knocking: Knocking) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        refused = await tenant.delete(f"/v1/calls/{context.call}")
    assert refused.status_code == 409
    assert await knocking.gateway.logs.store.whole(context.call) != []


@postgres
async def test_another_orgs_key_and_the_other_world_erase_nothing(knocking: Knocking) -> None:
    context = await a_sealed_call(knocking)
    stranger = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    theirs = await issued(knocking.gateway.connections.pool, stranger.id, "sandbox", KEY_SCOPES)
    async with knocking.http(theirs) as other:
        refused = await other.delete(f"/v1/calls/{context.call}")
    async with knocking.http(knocking.app["production"]) as production:
        elsewhere = await production.delete(f"/v1/calls/{context.call}")
    assert (refused.status_code, elsewhere.status_code) == (404, 404)
    assert await knocking.gateway.logs.store.whole(context.call) != []


@postgres
async def test_a_persons_read_and_a_servers_are_written_once_an_hour_and_the_workers_is_not(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    context = await a_logged_call(knocking, "greeted")
    ana, secret = await a_developer(knocking, "ana@clinica.test")
    sandbox = {"pinecall-env": "sandbox"}
    async with knocking.http(secret) as person:
        for _ in range(2):
            read = await person.get(f"/v1/calls/{context.call}/state", headers=sandbox)
            assert read.status_code == 200
    async with knocking.http(knocking.app["sandbox"]) as server:
        assert (await server.get(f"/v1/calls/{context.call}/events")).status_code == 200
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        assert (await worker.get(f"/v1/calls/{context.call}/state")).status_code == 200
    pool = knocking.gateway.connections.pool
    servers = await keys.verify(pool, knocking.app["sandbox"])
    assert servers is not None
    rows = await reads.of_org(pool, knocking.org.id)
    assert sorted((row.subject, row.what, row.reader) for row in rows) == sorted(
        [(context.call, "log", ana), (context.call, "log", servers.key.key_id)]
    )
    await app.close()


@postgres
async def test_a_web_call_opens_once_and_only_where_its_token_was_minted(
    knocking: Knocking,
) -> None:
    context = replace(a_call(knocking, channel="web"), metadata={"scope": "talk"})
    minted = tokens.MintedToken(
        context.call, knocking.org.id, "sandbox", AGENT, "talk", time.time() + 60
    )
    await tokens.minted(knocking.gateway.connections.pool, minted)
    another_org = replace(context, route=replace(context.route, org="org_other"))
    another_world = replace(context, route=replace(context.route, env="production"))
    async with (
        knocking.http(knocking.fleet["sandbox"]) as worker,
        knocking.http(knocking.fleet["production"]) as productions,
    ):
        refused = [
            await worker.post(
                "/v1/calls", json=OpenCallRequest(agent=AGENT, context=another_org).written()
            ),
            await productions.post(
                "/v1/calls", json=OpenCallRequest(agent=AGENT, context=another_world).written()
            ),
        ]
        opened = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
        again = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
    assert [answer.status_code for answer in refused] == [404, 404]
    assert opened.status_code == 200
    assert again.status_code == 409
    assert "a token opens one call, once" in again.json()["detail"]


# What a stage's fallbacks are ordered by: each call a vendor failed, however often it failed it.
@postgres
async def test_a_vendor_that_failed_a_call_is_counted_once_for_it_at_the_append_doors(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    failed: JsonObject = {
        "code": "component_failed",
        "message": "type='stt_error' label='livekit.plugins.deepgram.stt.STT' recoverable=True",
        "recoverable": True,
    }
    down: JsonObject = {
        "stage": "tts",
        "vendor": "cartesia",
        "model": "sonic-3",
        "available": False,
        "serving": "elevenlabs",
        "serving_model": "eleven_flash_v2_5",
    }
    batch: JsonObject = {
        "after": 0,
        "entries": [
            {"type": "error", "data": failed, "ts": 0.1},
            {"type": "error", "data": failed, "ts": 0.2},
            {"type": "vendor.switched", "data": down, "ts": 0.3},
            {"type": "vendor.switched", "data": {**down, "available": True}, "ts": 0.4},
        ],
    }
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        assert (await worker.post(f"/v1/calls/{context.call}/entries", json=batch)).is_success
    failures = knocking.gateway.counters.failures
    assert [call for _, call in failures["deepgram"]] == [context.call, context.call]
    assert [call for _, call in failures["cartesia"]] == [context.call]


ENDED = {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 10.0, "duration_s": 42.0}


# The summary and the score are the gateway's own words: nobody sends them, the fleet included.
@postgres
async def test_what_the_gateway_writes_itself_is_refused_at_every_append_door(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking)
    summary: JsonObject = {"type": "call.summary", "data": {"reason": "caller_hung_up"}}
    timed: JsonObject = {**summary, "ts": 1.0}
    for key in (knocking.fleet["sandbox"], knocking.app["sandbox"]):
        async with knocking.http(key) as other:
            single = await other.post(f"/v1/calls/{context.call}/events", json=summary)
            batch = await other.post(
                f"/v1/calls/{context.call}/entries", json={"after": 0, "entries": [timed]}
            )
        assert single.status_code == 403, single.text
        assert "written by the gateway itself" in single.json()["detail"]
        assert batch.status_code == 403
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert "call.summary" not in kinds


# A call the fleet opened is the fleet's to write and to seal: the org's own key reads it, and
# knocks at none of the worker's doors.
@postgres
async def test_a_fleet_opened_call_takes_no_worker_knock_from_the_orgs_own_key(
    knocking: Knocking,
) -> None:
    context = await a_logged_call(knocking, "one")
    entry: JsonObject = {"type": "custom", "data": {"name": "n", "data": {}}}
    async with knocking.http(knocking.app["sandbox"]) as own:
        appended = await own.post(f"/v1/calls/{context.call}/events", json=entry)
        sealed = await own.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="booked").written(),
        )
        read = await own.get(f"/v1/calls/{context.call}/events")
    assert (appended.status_code, sealed.status_code, read.status_code) == (404, 404, 200)
    assert not await knocking.gateway.logs.store.sealed(context.call)
    # Forgotten by this gateway and read back off the head, the answer is the same.
    knocking.gateway.live.close(context.call)
    async with knocking.http(knocking.app["sandbox"]) as own:
        again = await own.post(f"/v1/calls/{context.call}/events", json=entry)
    assert again.status_code == 404
