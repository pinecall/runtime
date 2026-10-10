"""Tests for the org's meters: the usage feed, a window's insights, and the limits."""

from pinecall.domain.names import JsonObject
from pinecall.domain.org import Quotas
from pinecall.providers import catalog
from pinecall.tenancy import admission
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import a_call, an_app
from tests.gateway.conftest import EVERY_HELD, judging

USAGE = "/v1/usage"
INSIGHTS = "/v1/insights"
SERIES = "/v1/insights/series"
LIMITS = "/v1/limits"


DRIFT = "/v1/insights/drift"


# One turn each way, whose reports name the vendor and the model of each stage.
TURNS: list[JsonObject] = [
    {
        "type": "turn.user",
        "data": {
            "speech_id": "s1",
            "text": "hola",
            "transcript_confidence": 0.9,
            "metrics": {
                "transcription_delay": 0.3,
                "stt_metadata": {"model_provider": "acme", "model_name": "acme-ears"},
            },
        },
    },
    {
        "type": "turn.agent",
        "data": {
            "speech_id": "s1",
            "text": "buenas",
            "interrupted": False,
            "metrics": {
                "llm_node_ttft": 0.6,
                "llm_metadata": {"model_provider": "acme", "model_name": "acme-1"},
            },
        },
    },
]


async def a_sealed_call(knocking: Knocking, turns: list[JsonObject] | None = None) -> str:
    """A call of the org, opened by the worker, its turns written, ended and sealed; its id."""
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        for turn in turns or []:
            await worker.post(f"/v1/calls/{context.call}/events", json=turn)
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
    assert set(rows[0]) >= {
        "minutes",
        "messages",
        "input_tokens",
        "cost_usd",
        "judge_calls",
        "evals",
        "simulated",
    }
    assert (rows[0]["minutes"], rows[0]["simulated"], rows[0]["evals"]) == (1.5, False, 0)
    assert page.json()["totals"]["calls"] == 1
    assert page.json()["totals"]["minutes"] == 1.5
    assert page.json()["totals"]["simulations"] == 0
    [score] = rest.json()["rows"]
    assert (score["type"], score["simulated"]) == ("call.score", False)
    assert isinstance(score["evals"], int)
    assert rest.json()["totals"]["calls"] == 0
    assert rest.json()["totals"]["evals"] == score["evals"]
    await app.close()


@postgres
async def test_the_feed_of_an_org_with_nothing_metered_is_empty_and_points_nowhere(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        page = await org.get(USAGE)
    assert page.json() == {"rows": [], "totals": None, "next": None}


@postgres
async def test_a_days_series_is_the_window_day_by_day(knocking: Knocking) -> None:
    app = await an_app(knocking)
    await a_sealed_call(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        week = await org.get(SERIES, params={"day": "1970-01-01", "days": 7})
        wrong = await org.get(SERIES, params={"days": 3})
    assert week.status_code == 200
    body = week.json()
    assert (body["day"], body["days"], body["agent"]) == ("1970-01-01", 7, None)
    assert [day["day"] for day in body["series"]][-1] == "1970-01-01"
    assert [day["calls"] for day in body["series"]] == [0, 0, 0, 0, 0, 0, 1]
    today = body["series"][-1]
    assert today["endings"] == [{"reason": "caller_hung_up", "count": 1}]
    assert {stage["stage"] for stage in today["stages"]} <= {"stt", "llm", "tts"}
    assert wrong.status_code == 400
    await app.close()


@postgres
async def test_a_days_insights_count_the_scopes_calls_and_the_orgs_month(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    pool = knocking.gateway.connections.pool
    await catalog.configure(pool, judging(*EVERY_HELD))
    await a_sealed_call(knocking)
    await admission.set_quotas(pool, knocking.org.id, "sandbox", Quotas(budget_usd=50))
    async with knocking.http(knocking.app["sandbox"]) as org:
        today = await org.get(INSIGHTS, params={"day": "1970-01-01"})
        empty = await org.get(INSIGHTS, params={"day": "2001-01-01"})
    assert today.status_code == 200
    body = today.json()
    assert (body["day"], body["days"], body["timezone"]) == ("1970-01-01", 1, "UTC")
    assert body["conversations"] == {"now": 1, "before": 0}
    assert (body["judged"], body["passed"], body["escalated"]) == (1, 1, 0)
    assert body["endings"] == [{"reason": "caller_hung_up", "count": 1}]
    assert [(day["day"], day["phone"]) for day in body["series"]] == [("1970-01-01", 1)]
    assert body["channels"] == {"phone": 1, "web": 0, "whatsapp": 0}
    assert body["sessions_total"] == 1
    assert body["resolved_rate"] == 1.0
    assert body["budget"] == {"limit_usd": 50, "spent_usd_month": 0.0}
    assert [agent["slug"] for agent in body["agents"]] == [AGENT]
    spend = body["agents"][0]["spend"]
    assert set(spend) == {
        "llm_usd",
        "stt_usd",
        "tts_usd",
        "phone_usd",
        "platform_usd",
        "minutes",
        "per_minute_usd",
    }
    assert spend["minutes"] > 0
    assert spend["per_minute_usd"] is not None
    assert empty.json()["conversations"] == {"now": 0, "before": 0}
    assert empty.json()["stages"] == []
    await app.close()


@postgres
async def test_a_week_of_one_agent_counts_its_seven_days_against_the_week_before(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    await a_sealed_call(knocking, TURNS)
    async with knocking.http(knocking.app["sandbox"]) as org:
        week = await org.get(INSIGHTS, params={"day": "1970-01-07", "days": 7, "agent": AGENT})
        later = await org.get(INSIGHTS, params={"day": "1970-01-14", "days": 7})
        odd = await org.get(INSIGHTS, params={"days": 3})
    assert week.status_code == 200, week.text
    body = week.json()
    assert (body["day"], body["days"], body["conversations"]) == (
        "1970-01-07",
        7,
        {"now": 1, "before": 0},
    )
    assert [day["day"] for day in body["series"]][0::6] == ["1970-01-01", "1970-01-07"]
    assert [day["phone"] for day in body["series"]] == [1, 0, 0, 0, 0, 0, 0]
    assert [row["stage"] for row in body["stages"]] == ["llm", "stt"]
    assert later.json()["conversations"] == {"now": 0, "before": 1}
    assert odd.status_code == 400
    assert "not a window" in odd.json()["detail"]
    await app.close()


@postgres
async def test_a_days_stages_and_an_agents_drift_are_read_from_what_the_seal_counted(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    await a_sealed_call(knocking, TURNS)
    async with knocking.http(knocking.app["sandbox"]) as org:
        today = await org.get(INSIGHTS, params={"day": "1970-01-01"})
        moved = await org.get(
            DRIFT, params={"agent": AGENT, "before": "1969-12-31", "after": "1970-01-01"}
        )
        by_version = await org.get(DRIFT, params={"agent": AGENT, "before": "v1", "after": "v2"})
        no_day = await org.get(DRIFT, params={"agent": AGENT, "before": "yesterday"})
        one_version = await org.get(DRIFT, params={"agent": AGENT, "after": "v2"})
    stages = [(row["stage"], row["vendor"], row["turns"]) for row in today.json()["stages"]]
    assert stages == [("llm", "acme", 1), ("stt", "acme", 1)]
    assert moved.status_code == 200
    body = moved.json()
    assert (body["before"]["day"], body["after"]["day"]) == ("1969-12-31", "1970-01-01")
    ears = next(row for row in body["stages"] if row["stage"] == "stt")
    assert (ears["before"], ears["after"]["turns"], ears["median_moved_s"]) == (None, 1, None)
    assert body["after"]["versions"] == []
    assert by_version.json()["stages"] == []
    assert (no_day.status_code, one_version.status_code) == (400, 400)
    assert "neither a day" in no_day.json()["detail"]
    await app.close()


@postgres
async def test_the_limits_say_each_quota_against_what_the_world_used(knocking: Knocking) -> None:
    app = await an_app(knocking)
    pool = knocking.gateway.connections.pool
    await catalog.configure(pool, judging(*EVERY_HELD))
    await a_sealed_call(knocking)
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
