"""Tests for an eval run: one per agent, its matrix, and the table it is stored in."""

import pytest

from pinecall.domain.errors import Conflict
from pinecall.domain.scope import Scope
from pinecall.evals import runs
from pinecall.evals.case import case_of
from pinecall.evals.judges import golden_judges
from pinecall.evals.runs import Runner, cell_of, matrix_of, score
from pinecall.postgres.pool import Pool
from pinecall.wire.parts import ModelConfig
from pinecall.wire.rest.evals import Golden, JudgeScore, OpenedCall, ScoreRow
from tests.conftest import postgres
from tests.evals.conftest import A_SUMMARY, THE_CLINIC, before_the_yes, confirmed, entry

HAIKU = "anthropic/claude-haiku-4-5"


SONNET = "anthropic/claude-sonnet-4-6"


CLINIC = Scope("org_clinic", "sandbox")


def scored(metric: str, *, passed: bool, calls: int = 0) -> JudgeScore:
    return JudgeScore(
        metric=metric,
        score=1.0 if passed else 0.0,
        passed=passed,
        reason="r",
        criteria="c",
        judge_calls=calls,
    )


def cell(model: str, golden: str, *scores: JudgeScore) -> ScoreRow:
    return ScoreRow(model=model, golden=golden, scores=list(scores), summary=None)


async def test_a_second_run_on_one_agent_is_refused_and_that_agent_is_freed_after() -> None:
    runner = Runner()
    async with runner.alone("run_first", "clinica-norte"):
        assert runner.running["clinica-norte"] == "run_first"
        with pytest.raises(Conflict) as refused:
            async with runner.alone("run_second", "clinica-norte"):
                pass
    assert "run_first" in str(refused.value)
    assert "clinica-norte" in str(refused.value)
    assert runner.running == {}


async def test_a_run_on_one_agent_leaves_every_other_agent_free_to_be_run() -> None:
    runner = Runner()
    async with runner.alone("run_the_clinics", "clinica-norte"):
        async with runner.alone("run_the_shops", "tienda-sur"):
            assert runner.running == {
                "clinica-norte": "run_the_clinics",
                "tienda-sur": "run_the_shops",
            }
        assert runner.running == {"clinica-norte": "run_the_clinics"}


def test_the_axes_come_out_in_the_order_the_runs_came_in() -> None:
    matrix = matrix_of(
        [
            cell(HAIKU, "confirmed", scored("consent", passed=True)),
            cell(HAIKU, "before-the-yes", scored("consent", passed=False)),
            cell(
                SONNET, "confirmed", scored("consent", passed=True), scored("register", passed=True)
            ),
        ]
    )
    assert matrix.models == [HAIKU, SONNET]
    assert matrix.goldens == ["confirmed", "before-the-yes"]
    assert matrix.metrics == ["consent", "register"]


def test_the_findings_are_every_judge_that_did_not_hold_with_the_cell_it_broke_on() -> None:
    matrix = matrix_of(
        [
            cell(HAIKU, "a", scored("consent", passed=False), scored("says", passed=True)),
            cell(SONNET, "a", scored("consent", passed=True), scored("says", passed=False)),
        ]
    )
    assert [(failure.model, failure.golden, failure.metric) for failure in matrix.failures] == [
        (HAIKU, "a", "consent"),
        (SONNET, "a", "says"),
    ]


def test_the_judge_calls_of_the_matrix_are_every_cells_added() -> None:
    matrix = matrix_of(
        [
            cell(HAIKU, "a", scored("grounded", passed=True, calls=1)),
            cell(SONNET, "a", scored("grounded", passed=True, calls=2)),
        ]
    )
    assert matrix.judge_calls == 3


async def test_a_matrix_of_code_judges_asks_nothing_and_says_so() -> None:
    case = case_of(confirmed(), THE_CLINIC)
    golden = Golden.model_validate({"name": "confirmed", "expect": {"says": ["reservada"]}})
    scores = await score(golden_judges(golden, case), case, None)
    assert [(judge_score.metric, judge_score.score) for judge_score in scores] == [
        ("consent", 1.0),
        ("says", 1.0),
    ]
    assert matrix_of([cell(HAIKU, "confirmed", *scores)]).judge_calls == 0


async def test_every_cell_carries_the_calls_own_summary_and_never_a_number_of_its_own() -> None:
    case = case_of([*confirmed(), entry(9, "call.summary", A_SUMMARY)], THE_CLINIC)
    opened = OpenedCall(golden="confirmed", model=HAIKU, call="c1")
    row = cell_of(opened, case, [scored("consent", passed=True)], None)
    assert row.summary is not None
    assert row.summary["outcome"] == "booked"


def test_a_cell_that_held_carries_no_requests_and_one_that_broke_carries_them() -> None:
    case = case_of(before_the_yes(), THE_CLINIC)
    opened = OpenedCall(golden="g", model=HAIKU, call="c1")
    passing_cell = cell_of(opened, case, [scored("consent", passed=True)], [{"messages": []}])
    broken_cell = cell_of(opened, case, [scored("consent", passed=False)], [{"messages": []}])
    spoken_cell = cell_of(opened, case, [scored("consent", passed=False)], None)
    assert "asked" not in passing_cell.written()
    assert broken_cell.written()["asked"] == [{"messages": []}]
    assert spoken_cell.written()["asked"] is None


def test_a_column_is_the_model_run_or_the_agents_own() -> None:
    assert runs.column_of(None) == "declared"
    assert runs.column_of(ModelConfig(provider="anthropic", model="claude-haiku-4-5")) == HAIKU


def test_a_run_that_stops_early_keeps_its_cells_and_says_why() -> None:
    run = runs.judged(
        runs.new_run("clinica-norte"), [cell(HAIKU, "a", scored("consent", passed=True))]
    )
    stopped = runs.stopped(run, "the app left")
    assert (stopped.status, stopped.error) == ("failed", "the app left")
    assert stopped.matrix is not None
    assert stopped.finished_at is not None


async def written_runs(pool: Pool) -> list[str]:
    ids: list[str] = []
    for agent, started_at in (
        ("clinica-norte", 100.0),
        ("tienda-sur", 200.0),
        ("clinica-norte", 300.0),
        ("tienda-sur", 400.0),
    ):
        run = runs.new_run(agent).model_copy(update={"started_at": started_at, "status": "done"})
        await runs.put(pool, CLINIC, run)
        ids.append(run.id)
    return ids


@postgres
async def test_the_runs_are_read_back_newest_first_and_one_by_its_own_id(pool: Pool) -> None:
    old_clinic, old_shop, new_clinic, new_shop = await written_runs(pool)
    listed = await runs.listed(pool, CLINIC, agent=None, since=0.0, limit=20)
    assert [run.id for run in listed] == [new_shop, new_clinic, old_shop, old_clinic]
    found = await runs.of(pool, CLINIC, old_clinic)
    assert found is not None
    assert (found.agent, found.status) == ("clinica-norte", "done")


@postgres
async def test_the_runs_of_one_agent_never_carry_another_agents_run(pool: Pool) -> None:
    old_clinic, _, new_clinic, _ = await written_runs(pool)
    listed = await runs.listed(pool, CLINIC, agent="clinica-norte", since=0.0, limit=2)
    assert [run.id for run in listed] == [new_clinic, old_clinic]


@postgres
async def test_since_and_agent_narrow_the_same_list_together(pool: Pool) -> None:
    _, _, new_clinic, _ = await written_runs(pool)
    listed = await runs.listed(pool, CLINIC, agent="clinica-norte", since=150.0, limit=20)
    assert [run.id for run in listed] == [new_clinic]


@postgres
async def test_another_orgs_runs_and_the_other_worlds_are_never_listed_nor_read(
    pool: Pool,
) -> None:
    ids = await written_runs(pool)
    shop = Scope("org_shop", "sandbox")
    production = Scope(CLINIC.org, "production")
    assert await runs.listed(pool, shop, agent=None, since=0.0, limit=20) == []
    assert await runs.listed(pool, production, agent=None, since=0.0, limit=20) == []
    assert await runs.of(pool, shop, ids[0]) is None


@postgres
async def test_a_run_restored_with_no_org_is_listed_by_nobody(pool: Pool) -> None:
    run = runs.new_run("clinica-norte")
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO eval_runs (id, agent, started_at, status, document) "
            "VALUES (%(id)s, 'clinica-norte', 1.0, 'done', %(document)s)",
            {"id": run.id, "document": run.model_dump_json()},
        )
    assert await runs.listed(pool, CLINIC, agent=None, since=0.0, limit=20) == []


@postgres
async def test_a_run_written_again_replaces_the_row_whole(pool: Pool) -> None:
    run = runs.new_run("clinica-norte")
    await runs.put(pool, CLINIC, run)
    await runs.put(pool, CLINIC, runs.finished(run))
    kept = await runs.of(pool, CLINIC, run.id)
    assert kept is not None
    assert kept.status == "done"
