"""The matrix: every golden under every model, judged by every judge, in one table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from livekit.agents.evals import EvaluationResult, Evaluator
from livekit.agents.llm import LLM

from pinecall.evals.case import Case
from pinecall.evals.judges.model import Counted


@dataclass(frozen=True)
class Spoken:
    """One golden spoken under one model, and the case its log reduced to."""

    model: str
    golden: str
    case: Case


@dataclass(frozen=True)
class Score:
    """What one judge answered about one case: the number, the sentence, and what it was asked."""

    metric: str
    # livekit's own arithmetic over a verdict: pass 1.0, maybe 0.5, fail 0.0
    # (evals/evaluation.py:41-52).
    score: float
    passed: bool
    reason: str
    # The question that was answered, as livekit hangs it on every judgment it makes.
    criteria: str
    # How many questions this judgment actually put to a provider. A count and never a price: a
    # judge's tokens are an LLM row like any other and are priced where every other LLM row is.
    judge_calls: int


@dataclass(frozen=True)
class Run:
    """One cell: a golden, a model, every judge's answer, and the call's own summary beside it."""

    model: str
    golden: str
    scores: tuple[Score, ...]
    # `call.summary` verbatim: duration, turns, usage rows and cost. Nothing here is recomputed —
    # what the log did not measure, the matrix does not know.
    summary: Mapping[str, Any] | None

    def at(self, metric: str) -> Score | None:
        """This cell's answer for one judge, or None when that judge was not run over it."""
        return next((score for score in self.scores if score.metric == metric), None)


@dataclass(frozen=True)
class Matrix:
    """Models down one axis, goldens down the other, one judge's score in every cell."""

    runs: tuple[Run, ...]

    @property
    def models(self) -> tuple[str, ...]:
        """Every model a golden was run under, in the order the runs came in."""
        return _in_order(run.model for run in self.runs)

    @property
    def goldens(self) -> tuple[str, ...]:
        """Every golden that was run, in the order the runs came in."""
        return _in_order(run.golden for run in self.runs)

    @property
    def metrics(self) -> tuple[str, ...]:
        """Every judge that answered something, in the order it was declared."""
        return _in_order(score.metric for run in self.runs for score in run.scores)

    @property
    def judge_calls(self) -> int:
        """How many questions judging this whole matrix put to a model. Zero is the happy path."""
        return sum(score.judge_calls for run in self.runs for score in run.scores)

    def at(self, model: str, golden: str) -> Run | None:
        """One cell, or None when that golden was never run under that model."""
        return next((run for run in self.runs if run.model == model and run.golden == golden), None)

    def failures(self) -> tuple[tuple[Run, Score], ...]:
        """Every judge that did not hold, with the run it broke on: the whole finding list."""
        return tuple((run, score) for run in self.runs for score in run.scores if not score.passed)


# Who picks the judges is the caller's business, and there is one rule about that list: a policy
# that answers from the evidence and asks nobody rides in every one of them, whatever the golden
# declared. Consent is the first such policy, so `consent` is a column of every matrix this
# package draws and never one a suite opted into. See docs/decisions/pinecall-test.md.
async def a_matrix(spoken: Sequence[Spoken], judges: Sequence[Evaluator], llm: LLM[Any]) -> Matrix:
    """Every judge over every case. The judges answer; nothing here runs a turn or scores one."""
    return Matrix(runs=tuple([await _a_run(one, judges, llm) for one in spoken]))


async def _a_run(one: Spoken, judges: Sequence[Evaluator], llm: LLM[Any]) -> Run:
    """One case under every judge, in the order the judges were declared."""
    scores = [await _a_score(judge, one.case, llm) for judge in judges]
    return Run(model=one.model, golden=one.golden, scores=tuple(scores), summary=one.case.summary)


# The judge model is wrapped per score and thrown away, so the count belongs to the one judgment
# that spent it: a shared counter would report the whole matrix's questions in every cell.
async def _a_score(judge: Evaluator, case: Case, llm: LLM[Any]) -> Score:
    """One judge over one case, read off the judgment livekit's own result type scores."""
    counted = Counted(llm)
    # `Evaluator.evaluate` takes livekit's own `LLM`, left bare in its signature (evals/judge.py).
    result = await judge.evaluate(  # pyright: ignore[reportUnknownMemberType]
        chat_ctx=case.chat_ctx, llm=counted
    )
    return Score(
        metric=judge.name,
        score=EvaluationResult(judgments={judge.name: result}).score,
        passed=result.passed,
        reason=result.reasoning,
        criteria=result.instructions,
        judge_calls=counted.calls,
    )


def _in_order(names: Any) -> tuple[str, ...]:
    """The distinct names, in the order they first appeared: a matrix reads in the order it ran."""
    return tuple(dict.fromkeys(names))
