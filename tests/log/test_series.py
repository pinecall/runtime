"""Tests for a window as series: each day's calls, endings, cost, latencies, judges and tools."""

from datetime import UTC, date, datetime

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log import drift
from pinecall.log.series import series_window
from pinecall.log.store import Store
from pinecall.tenancy.orgs import create
from pinecall.wire.events import CallScore
from tests.conftest import postgres
from tests.log.conftest import THE_DAY, ACall, judgment, logged_call

DAY = datetime.fromtimestamp(THE_DAY, UTC).date()


def answered(ttft: float, ttfb: float) -> tuple[str, JsonObject]:
    report: JsonObject = {
        "llm_node_ttft": ttft,
        "tts_node_ttfb": ttfb,
        "llm_metadata": {"model_provider": "anthropic", "model_name": "claude-haiku-5-5"},
        "tts_metadata": {"model_provider": "cartesia", "model_name": "sonic-3"},
    }
    turn: JsonObject = {"speech_id": "a", "text": "claro", "interrupted": False, "metrics": report}
    return ("turn.agent", turn)


def tool_result(error: str | None) -> tuple[str, JsonObject]:
    return ("tool.result", {"call_id": "t1", "name": "book", "output": None, "error": error})


async def an_org(store: Store, slug: str) -> str:
    """An org that exists: the fold's rows reference it."""
    return (await create(store.pool, slug, "Series")).id


async def folded(store: Store, org: str, went: ACall) -> str:
    """A call logged and sealed, then folded as the seal folds it."""
    call = await logged_call(store, org, went)
    entries = await store.whole(call)
    score = CallScore.model_validate({"judges": list(went.judges), "judge_calls": 0})
    assert await drift.fold(store.pool, call, entries, score)
    return call


@postgres
async def test_a_day_says_its_calls_latencies_judges_tools_and_how_they_ended(
    store: Store, org: str
) -> None:
    org = await an_org(store, org)
    judged = (judgment("grounded", "held"), judgment("consent", "broken", "no"))
    logged = (answered(0.4, 0.1), answered(0.8, 0.2), tool_result(None), tool_result("boom"))
    await folded(store, org, ACall(judges=judged, cost=0.5, logged=logged))
    await folded(store, org, ACall(took_over=True, cost=0.25, logged=(answered(1.6, 0.3),)))
    await logged_call(store, org, ACall(scope=Scope(org, "sandbox", "m_dev"), cost=4.0))

    [day] = await series_window(store.pool, Scope(org), DAY, DAY, None)

    assert (day.day, day.calls, day.finished, day.escalated, day.spent) == (DAY, 2, 2, 1, 0.75)
    assert day.e2e_median == 1.5
    assert day.endings == [("caller_hung_up", 2)]
    assert [(stage.stage, stage.turns) for stage in day.stages] == [("llm", 3), ("tts", 3)]
    llm = next(stage for stage in day.stages if stage.stage == "llm")
    assert llm.median_s is not None
    assert 0.7 <= llm.median_s <= 0.9
    assert [(judge.name, judge.held, judge.judged) for judge in day.judges] == [
        ("consent", 0, 1),
        ("grounded", 1, 1),
    ]
    assert (day.tools_ran, day.tools_failed) == (2, 1)


@postgres
async def test_a_window_has_a_row_every_day_and_a_quiet_day_is_zeros_and_gaps(
    store: Store, org: str
) -> None:
    org = await an_org(store, org)
    await folded(store, org, ACall(cost=0.5))
    first, last = date(1969, 12, 31), date(1970, 1, 2)

    days = await series_window(store.pool, Scope(org), first, last, None)

    assert [day.day for day in days] == [first, DAY, last]
    assert [day.calls for day in days] == [0, 1, 0]
    assert (days[0].e2e_median, days[0].stages, days[0].judges) == (None, [], [])
    assert await series_window(store.pool, Scope(org), DAY, DAY, "other") == [
        days[0].__class__(day=DAY)
    ]
