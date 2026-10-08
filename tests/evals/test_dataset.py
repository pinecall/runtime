"""Tests for the dataset: a finished call made a golden, kept, picked for a run, forgotten."""

import pytest

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound
from pinecall.domain.names import JsonObject
from pinecall.evals import dataset
from pinecall.evals.dataset import Born, Promoted, expect_of, golden_of
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from pinecall.wire.frames import Entry
from pinecall.wire.rest.evals import CaseDecision, EvalCase, Expect
from pinecall.wire.scores import CallScore, Judgment, JudgmentEvidence
from tests.conftest import postgres
from tests.evals.conftest import BOOK, a_log, agent, before_the_yes, caller

AGENT = "clinica-norte"


def test_a_golden_is_the_callers_lines_the_state_it_opened_in_and_the_apps_facts() -> None:
    log = a_log(
        ("state.changed", {"state": {"stage": "book"}, "changed": ["stage"]}),
        caller("Quiero el jueves"),
        ("event.received", {"name": "slot_freed", "data": {"at": "10:15"}, "source": "app"}),
        ("event.received", {"name": "clicked", "data": {}, "source": "participant"}),
        agent("Se liberó el de las 10:15."),
        ("state.changed", {"state": {"stage": "confirm"}, "changed": ["stage"]}),
        caller("Vale"),
        caller("   "),
    )
    golden = golden_of(log, "jueves", Expect(says=["10:15"]))
    bare = golden_of(a_log(caller("Hola")), "hola", Expect()).written()
    assert set(bare) == {"name", "input", "today", "expect", "promoted_from"}, (
        "nothing empty is written"
    )
    assert golden.input == ["Quiero el jueves", "Vale"]
    assert golden.state == {"stage": "book"}
    assert [(event.after_turn, event.name) for event in golden.events] == [(1, "slot_freed")]
    assert golden.promoted_from == "CA_8f4a2c"
    assert golden.today is not None
    assert golden.expect.says == ["10:15"]


def test_what_the_call_recalled_rides_the_golden_so_a_replay_knows_what_the_call_knew() -> None:
    recalled: JsonObject = {
        "ops": [
            {
                "op": "recall",
                "contact": "+34600000001",
                "facts": [{"id": "f1", "text": "Prefiere mañanas", "source": "CA_1"}],
                "took_ms": 3.0,
            }
        ]
    }
    log = a_log(caller("¿El martes?"), ("memory.ops", recalled), ("memory.ops", recalled))
    assert golden_of(log, "martes", Expect()).memory == ["Prefiere mañanas"]


def test_a_broken_verdict_says_what_must_not_happen_again_and_a_model_judge_is_named() -> None:
    log = before_the_yes()
    booked = next(entry.seq for entry in log if entry.type == "tool.call")
    score = a_score(
        breaking("consent", seqs=[booked]),
        breaking("grounded"),
        breaking("promises"),
        breaking("persona"),
        holding("disclosed"),
    )
    expect = expect_of(score.judges, log)
    assert expect.written() == {"not_tools": [BOOK.name], "grounded": True, "judges": ["promises"]}
    assert expect_of(a_score(breaking("promises")).judges, log).written() == {
        "judges": ["promises"]
    }
    assert expect.not_tools == [BOOK.name]
    assert expect.grounded is True
    assert expect.judges == ["promises"], "persona needs a simulated caller; a case has none"


def test_a_call_cut_at_a_seq_opens_in_the_state_it_was_in_there_and_plays_what_came_after() -> None:
    log = a_log(
        ("state.changed", {"state": {"stage": "identify"}, "changed": ["stage"]}),
        caller("Soy Marta"),
        ("state.changed", {"state": {"stage": "book"}, "changed": ["stage"]}),
        caller("El jueves"),
    )
    golden = golden_of(dataset.cut_at(log, 3), "jueves", Expect())
    assert (golden.state, golden.input) == ({"stage": "book"}, ["El jueves"])
    assert golden_of(dataset.cut_at(log, 0), "todo", Expect()).input == ["Soy Marta", "El jueves"]


def test_a_call_whose_caller_said_nothing_is_no_case() -> None:
    with pytest.raises(Conflict, match="no line of the caller's"):
        golden_of(a_log(agent("¿Hola?")), "silence", Expect())


@postgres
async def test_a_case_is_kept_once_by_name_and_a_run_picks_what_is_not_held_out(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    golden = golden_of(a_log(caller("Hola")), "hola", Expect())
    kept = await dataset.promoted(
        pool, golden, AGENT, Promoted(org.id, "production", "hola", "m_ana")
    )
    kept_out = Promoted(org.id, "production", "held", "m_ana", held_out=True)
    await dataset.promoted(pool, golden, AGENT, kept_out)
    with pytest.raises(Conflict, match="named hola already"):
        await dataset.promoted(pool, golden, AGENT, Promoted(org.id, "sandbox", "hola", "m_bo"))
    assert (kept.source_call, kept.source_env, kept.author) == ("CA_8f4a2c", "production", "m_ana")
    nightly = await dataset.picked(pool, org.id, AGENT, [], every=True)
    release = await dataset.picked(pool, org.id, AGENT, ["held"], every=True)
    assert [golden.name for golden in nightly] == ["hola"]
    assert [golden.name for golden in release] == ["held", "hola"]
    with pytest.raises(NotFound, match="no case named nobody"):
        await dataset.picked(pool, org.id, AGENT, ["nobody"], every=False)
    assert [case.name for case in await dataset.listed(pool, org.id, None)] == ["held", "hola"]
    await dataset.forgotten(pool, org.id, kept.id)
    with pytest.raises(NotFound):
        await dataset.forgotten(pool, org.id, kept.id)
    with pytest.raises(NotFound):
        await dataset.forgotten(pool, "another-org", kept.id)


@postgres
async def test_a_call_a_judge_broke_on_waits_as_a_pending_case_once_until_the_inbox_is_full(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    born = Born(org.id, "production", AGENT, version=4)
    score = a_score(breaking("promises"))
    log = a_log(caller("¿Me llaman mañana, por favor?"), agent("Le llamaremos mañana."))
    await dataset.kept_at_hangup(pool, log, score, born)
    await dataset.kept_at_hangup(pool, log, score, born)
    await dataset.kept_at_hangup(pool, log, a_score(holding("promises")), born)
    pending = await dataset.listed(pool, org.id, AGENT, "pending")
    assert [case.name for case in pending] == ["promises-me-llaman-manana-por-8f4a2c"]
    case = pending[0]
    assert (case.author, case.source_version, case.status) == ("the hang-up panel", 4, "pending")
    assert [(judgment.judge, judgment.reason) for judgment in case.broke] == [
        ("promises", "it broke")
    ]
    assert case.golden.expect.judges == ["promises"]
    for number in range(dataset.PENDING_AT_MOST):
        await dataset.kept_at_hangup(pool, a_call(number), score, born)
    assert await dataset.waiting(pool, org.id, AGENT) == dataset.PENDING_AT_MOST


@postgres
async def test_a_simulated_caller_is_meant_to_break_things_and_is_never_kept(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    started: JsonObject = {
        "channel": "web",
        "direction": "inbound",
        "from": "simulated_caller",
        "to": AGENT,
        "caller": None,
        "started_at": 1.0,
    }
    log = a_log(("call.started", started), caller("Hola"))
    await dataset.kept_at_hangup(
        pool, log, a_score(breaking("promises")), Born(org.id, "sandbox", AGENT, None)
    )
    assert await dataset.listed(pool, org.id, AGENT) == []


@postgres
async def test_a_person_decides_a_case_and_the_nightly_plays_only_what_was_approved(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    born = Born(org.id, "production", AGENT, None)
    for number in range(3):
        await dataset.kept_at_hangup(pool, a_call(number), a_score(breaking("promises")), born)
    first, second, third = await dataset.listed(pool, org.id, AGENT, "pending")
    approved = await dataset.decided(
        pool, org.id, first.id, CaseDecision(status="approved"), "m_ana"
    )
    await dataset.decided(pool, org.id, second.id, CaseDecision(status="dismissed"), "m_ana")
    assert (approved.status, approved.decided_by) == ("approved", "m_ana")
    assert await dataset.waiting(pool, org.id, AGENT) == 1
    nightly = await dataset.picked(pool, org.id, AGENT, [], every=True)
    assert [golden.name for golden in nightly] == [first.name]
    named = await dataset.picked(pool, org.id, AGENT, [third.name], every=False)
    assert [golden.name for golden in named] == [third.name], "a pending case is reproduced by name"
    in_the_repo = CaseDecision(kept_in_repo=True)
    await dataset.decided(pool, org.id, first.id, in_the_repo, "m_ana")
    assert await dataset.picked(pool, org.id, AGENT, [], every=True) == []
    with pytest.raises(NotFound):
        await dataset.decided(pool, "another-org", first.id, in_the_repo, "m_ana")


def test_a_judge_called_wrong_is_one_that_broke_and_a_note_needs_one() -> None:
    case = EvalCase.model_validate(
        {
            "id": "case_1",
            "agent": AGENT,
            "name": "promises-x",
            "golden": {"name": "promises-x"},
            "source_call": "CA_1",
            "source_env": "production",
            "held_out": False,
            "author": "the hang-up panel",
            "created_at": 1.0,
            "broke": [{"judge": "promises", "reason": "it broke"}],
        }
    )
    dataset.check_decision(case, CaseDecision(status="dismissed", judge_was_wrong="promises"))
    with pytest.raises(DeclarationRefused, match="send it with status dismissed"):
        dataset.check_decision(case, CaseDecision(status="approved", judge_was_wrong="promises"))
    with pytest.raises(DeclarationRefused, match="send judge_was_wrong"):
        dataset.check_decision(case, CaseDecision(status="dismissed", note="it did book"))
    with pytest.raises(DeclarationRefused, match="did not break on grounded"):
        dataset.check_decision(case, CaseDecision(status="dismissed", judge_was_wrong="grounded"))


def a_call(number: int) -> list[Entry]:
    """Another call that broke, its caller's own words, its own id."""
    return [
        line.model_copy(update={"call": f"CA_{number:06d}"})
        for line in a_log(caller(f"Llamada número {number}"), agent("Le llamaremos."))
    ]


def a_score(*judgments: Judgment) -> CallScore:
    """A call.score of these verdicts."""
    passed = not any(judgment.verdict == "broken" for judgment in judgments)
    return CallScore(judges=list(judgments), judge_calls=0, passed=passed)


def breaking(name: str, *, seqs: list[int] | None = None) -> Judgment:
    """A judge that broke."""
    evidence = JudgmentEvidence(seqs=seqs or [])
    return Judgment(name=name, verdict="broken", criteria="", reason="it broke", evidence=evidence)


def holding(name: str) -> Judgment:
    """A judge that held."""
    evidence = JudgmentEvidence(seqs=[])
    return Judgment(name=name, verdict="held", criteria="", reason="it held", evidence=evidence)
