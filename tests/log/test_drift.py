"""Tests for drift: a sealed call counted into its day once, read by day, by version, compared."""

from dataclasses import replace
from datetime import date

import pytest

from pinecall.domain.agent import Versions
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log import drift
from pinecall.log.drift import Side
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.wire.scores import CallScore
from tests.conftest import postgres
from tests.log.conftest import AGENT, ACall, judgment, logged_call

THE_DAY = date(1970, 1, 1)

ORG = "insert into orgs (id, slug, name) values (%(org)s, %(org)s, %(org)s)"

JUDGED = "select judge, held, broken from judge_days where org = %(org)s order by judge"

STAGED = "select count(*) as rows from stage_days where org = %(org)s"


@pytest.fixture
async def an_org(pool: Pool, org: str) -> str:
    """An org whose row exists, as every call's org does behind the doors."""
    async with pool.connection() as connection:
        await connection.execute(ORG, {"org": org})
    return org


def heard(delay: float, confidence: float, vendor: str, model: str) -> JsonObject:
    """A caller's turn whose report names the ears it ran on."""
    report: JsonObject = {
        "transcription_delay": delay,
        "stt_metadata": {"model_provider": vendor, "model_name": model},
    }
    return {"speech_id": "u", "text": "sí", "transcript_confidence": confidence, "metrics": report}


def answered(ttft: float, ttfb: float) -> JsonObject:
    """An agent's turn whose report names the model and the voice it ran on."""
    report: JsonObject = {
        "llm_node_ttft": ttft,
        "tts_node_ttfb": ttfb,
        "llm_metadata": {"model_provider": "anthropic", "model_name": "claude-haiku-5-5"},
        "tts_metadata": {"model_provider": "cartesia", "model_name": "sonic-3"},
    }
    return {"speech_id": "a", "text": "claro", "interrupted": False, "metrics": report}


async def sealed_with(store: Store, org: str, went: ACall, turns: list[JsonObject]) -> str:
    """A call logged with these turns and a score, sealed and folded as the seal folds it."""
    call = await logged_call(store, org, replace(went, ended=False))
    for turn in turns:
        kind = "turn.user" if "transcript_confidence" in turn else "turn.agent"
        await store.append(call, AGENT, kind, turn, ephemeral=False)
    score: JsonObject = {"judges": list(went.judges), "judge_calls": 0}
    await store.append(call, AGENT, "call.score", score, ephemeral=False)
    await store.seal(call)
    entries = await store.whole(call)
    assert await drift.fold(store.pool, call, entries, CallScore.model_validate(score))
    return call


async def judged(store: Store, org: str) -> list[tuple[str, int, int]]:
    """Each judge's row: its name, how many held, how many broke."""
    async with store.pool.connection() as connection:
        rows = await (await connection.execute(JUDGED, {"org": org})).fetchall()
    return [(row["judge"], row["held"], row["broken"]) for row in rows]


@postgres
async def test_a_call_is_counted_once_and_judged_again_its_verdicts_are_replaced(
    store: Store, an_org: str
) -> None:
    verdicts = (judgment("consent", "held"), judgment("grounded", "broken"))
    call = await sealed_with(store, an_org, ACall(judges=verdicts), [answered(0.5, 0.25)])
    entries = await store.whole(call)
    first = CallScore.model_validate(entries[-1].data)
    assert await drift.fold(store.pool, call, entries, first), "a seal knocked twice counts once"
    assert await judged(store, an_org) == [("consent", 1, 0), ("grounded", 0, 1)]
    again = {"judges": [judgment("consent", "held"), judgment("grounded", "held")]}
    assert await drift.fold(
        store.pool, call, entries, CallScore.model_validate(again | {"judge_calls": 1})
    )
    assert await judged(store, an_org) == [("consent", 1, 0), ("grounded", 1, 0)]
    async with store.pool.connection() as connection:
        staged = await (await connection.execute(STAGED, {"org": an_org})).fetchone()
    assert staged is not None
    assert staged["rows"] == 2, "the stages were counted once, not again at the second fold"


@postgres
async def test_each_stage_of_a_window_is_read_by_vendor_and_model_every_version_added(
    store: Store, an_org: str
) -> None:
    turns = [heard(delay, sure, "soniox", "stt-rt-v5") for delay, sure in ((0.2, 0.5), (0.4, 1.0))]
    await sealed_with(store, an_org, ACall(versions=Versions(config=3)), turns)
    later = [heard(1.0, 0.75, "soniox", "stt-rt-v5"), heard(0.3, 0.95, "deepgram", "nova-3")]
    await sealed_with(
        store, an_org, ACall(versions=Versions(config=4)), [*later, answered(0.5, 0.25)]
    )
    elsewhere = Scope(an_org, "sandbox", "m_dev")
    await sealed_with(
        store, an_org, ACall(scope=elsewhere), [heard(9.0, 0.1, "soniox", "stt-rt-v5")]
    )
    stages = await drift.stages_of_window(store.pool, Scope(an_org), THE_DAY, THE_DAY, None)
    rows = [(stage.stage, stage.vendor, stage.model, stage.turns) for stage in stages]
    assert rows == [
        ("llm", "anthropic", "claude-haiku-5-5", 1),
        ("stt", "deepgram", "nova-3", 1),
        ("stt", "soniox", "stt-rt-v5", 3),
        ("tts", "cartesia", "sonic-3", 1),
    ]
    soniox = stages[2]
    assert soniox.median_s == pytest.approx(0.4, rel=0.05)
    assert soniox.p95_s == pytest.approx(1.0, rel=0.05)
    assert soniox.confidence == pytest.approx(0.75)
    assert stages[0].median_s == pytest.approx(0.5, rel=0.05)
    assert await drift.stages_of_window(store.pool, Scope(an_org), THE_DAY, THE_DAY, "other") == []
    next_day = date(1970, 1, 2)
    assert await drift.stages_of_window(store.pool, Scope(an_org), next_day, next_day, None) == []


@postgres
async def test_two_versions_compared_say_what_moved_most_first(store: Store, an_org: str) -> None:
    upheld = (judgment("consent", "held"), judgment("grounded", "held"))
    fast = [heard(0.2, 0.9, "soniox", "stt-rt-v5"), answered(0.5, 0.25)]
    await sealed_with(store, an_org, ACall(versions=Versions(config=3), judges=upheld), fast)
    worse = (judgment("consent", "held"), judgment("grounded", "broken"))
    slow = [heard(0.7, 0.9, "soniox", "stt-rt-v5"), answered(0.5, 0.25)]
    await sealed_with(store, an_org, ACall(versions=Versions(config=4), judges=worse), slow)
    before = await drift.tally(store.pool, Scope(an_org), AGENT, Side(version=3))
    after = await drift.tally(store.pool, Scope(an_org), AGENT, Side(version=4))
    assert (before.versions, after.versions) == ({3}, {4})
    judges, stages = drift.compared(before, after)
    assert [(judge.name, judge.moved) for judge in judges] == [("grounded", -1.0), ("consent", 0.0)]
    assert not judges[0].criteria_changed
    assert (stages[0].stage, stages[0].vendor) == ("stt", "soniox")
    assert stages[0].median_moved_s == pytest.approx(0.5, rel=0.1)
    day = await drift.tally(store.pool, Scope(an_org), AGENT, Side(day=THE_DAY))
    assert day.versions == {3, 4}
    assert day.judges["grounded"].pass_rate == 0.5


@postgres
async def test_a_rebuild_forgets_what_it_names_and_counts_every_sealed_call_again(
    store: Store, an_org: str
) -> None:
    verdicts = (judgment("consent", "held"),)
    await sealed_with(store, an_org, ACall(judges=verdicts), [answered(0.5, 0.25)])
    await logged_call(store, an_org, ACall(ended=False))
    async with store.pool.connection() as connection:
        await connection.execute(
            "update judge_days set held = 7 where org = %(org)s", {"org": an_org}
        )
    done = await drift.rebuild(store.pool, org=an_org, since=0.0)
    assert (done.calls, done.folded) == (1, 1), "a call still going is not counted"
    assert await judged(store, an_org) == [("consent", 1, 0)]
