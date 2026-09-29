"""Tests for the org's meters: the usage feed, one day's insights, and the limits."""

from pinecall.domain.org import Quotas
from pinecall.tenancy import admission
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import a_call, an_app

USAGE = "/v1/usage"
INSIGHTS = "/v1/insights"
LIMITS = "/v1/limits"


async def a_sealed_call(knocking: Knocking) -> str:
    """A call of the org, opened by the worker, ended and sealed; its id."""
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
                    "duration_s": 90.0,
                },
            },
        )
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="booked").written(),
        )
    return context.call


@postgres
async def test_the_feed_pages_the_orgs_metered_rows_with_totals_and_a_cursor(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    call = await a_sealed_call(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        page = await org.get(USAGE, params={"limit": 1})
        rest = await org.get(USAGE, params={"after": page.json()["next"]})
    assert page.status_code == 200
    rows = page.json()["rows"]
    assert [(row["call"], row["type"]) for row in rows] == [(call, "call.summary")]
    # Flat, as v1 wrote it: the console and any billing layer read these keys at the row's top.
    assert set(rows[0]) >= {"minutes", "messages", "input_tokens", "cost_usd", "judge_calls"}
    assert rows[0]["minutes"] == 1.5
    assert page.json()["totals"]["calls"] == 1
    assert page.json()["totals"]["minutes"] == 1.5
    assert [row["type"] for row in rest.json()["rows"]] == ["call.score"]
    assert rest.json()["totals"]["calls"] == 0
    await app.close()


@postgres
async def test_the_feed_of_an_org_with_nothing_metered_is_empty_and_points_nowhere(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        page = await org.get(USAGE)
    assert page.json() == {"rows": [], "totals": None, "next": None}


@postgres
async def test_a_days_insights_count_the_scopes_calls_and_the_orgs_month(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    await a_sealed_call(knocking)
    pool = knocking.gateway.connections.pool
    await admission.set_quotas(pool, knocking.org.id, "sandbox", Quotas(budget_usd=50))
    async with knocking.http(knocking.app["sandbox"]) as org:
        today = await org.get(INSIGHTS, params={"day": "1970-01-01"})
        empty = await org.get(INSIGHTS, params={"day": "2001-01-01"})
    assert today.status_code == 200
    body = today.json()
    assert (body["day"], body["timezone"]) == ("1970-01-01", "UTC")
    assert body["conversations"] == {"today": 1, "yesterday": 0}
    assert body["channels"] == {"phone": 1, "web": 0, "whatsapp": 0}
    assert body["sessions_total"] == 1
    assert body["resolved_rate"] == 1.0
    assert body["budget"] == {"limit_usd": 50, "spent_usd_month": 0.0}
    assert [agent["slug"] for agent in body["agents"]] == [AGENT]
    assert empty.json()["conversations"] == {"today": 0, "yesterday": 0}
    await app.close()


@postgres
async def test_the_limits_say_each_quota_against_what_the_world_used(knocking: Knocking) -> None:
    app = await an_app(knocking)
    await a_sealed_call(knocking)
    pool = knocking.gateway.connections.pool
    await admission.set_quotas(
        pool, knocking.org.id, "sandbox", Quotas(minutes=30, agents=2, lends=frozenset({"acme"}))
    )
    async with knocking.http(knocking.app["sandbox"]) as org:
        sandbox = await org.get(LIMITS)
    async with knocking.http(knocking.app["production"]) as org:
        production = await org.get(LIMITS)
    assert sandbox.status_code == 200
    body = sandbox.json()
    assert body["minutes"] == {"limit": 30, "used": 1.5}
    assert body["agents"] == {"limit": 2, "used": 1}
    assert body["seats"] == {"limit": None, "used": 0}
    assert (body["lends"], body["world"]) == (["acme"], "sandbox")
    assert production.json()["minutes"] == {"limit": None, "used": 0.0}
    await app.close()
