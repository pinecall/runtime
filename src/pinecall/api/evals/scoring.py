"""The judges a golden asks for, run over the call it opened. They judge; nothing here scores."""

from __future__ import annotations

from typing import Any

# The ONE place the gateway meets the evaluation distribution, which is why this module is
# imported at the door and never at startup: see api/evals/runs.py.
from pinecall import evals as rings
from pinecall.api.evals.conversation import Conversation
from pinecall.evals.goldens import Golden
from pinecall.types import AgentConfig


class Judging:
    """One run's judging: the one judge model, and the cells it has answered so far."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._judge = rings.a_judge()
        self._cells: list[Any] = []

    # One conversation at a time, the moment it ends: a person watching the run sees each golden's
    # verdict as it settles, and the matrix is the same one whether it is read half-way or whole.
    async def judged(self, one: Conversation) -> None:
        """This conversation under the judges its own golden asked for, added to the matrix."""
        case = _a_case(one, self._config)
        # livekit's `LLM` is `Generic[TEvent]` (llm/llm.py:115) and every signature that
        # takes one in this tree leaves it bare, so pyright reads the call as partially
        # unknown. The parameter is livekit's to name, not ours.
        measured = await rings.a_matrix(  # pyright: ignore[reportUnknownMemberType]
            [rings.Spoken(model=one.model, golden=one.golden.name, case=case, asked=one.asked)],
            _judges_for(one.golden, case),
            self._judge,
        )
        self._cells.extend(measured.runs)

    @property
    def matrix(self) -> dict[str, Any]:
        """Every cell answered so far, as one table of scores: the row's `matrix` at this moment."""
        return as_json(rings.Matrix(runs=tuple(self._cells)))


def as_json(matrix: Any) -> dict[str, Any]:
    """The matrix as the door answers it: the axes, a row per cell, and what judging it asked."""
    return {
        "models": list(matrix.models),
        "goldens": list(matrix.goldens),
        "metrics": list(matrix.metrics),
        # How many questions were put to a model, and never a price: a judge's tokens are an LLM
        # row like any other, and the calls' own cost is in each row's summary, in euros.
        "judge_calls": matrix.judge_calls,
        "runs": [_a_row(run) for run in matrix.runs],
        "failures": [
            {"model": run.model, "golden": run.golden, "metric": score.metric}
            for run, score in matrix.failures()
        ],
    }


def _a_row(run: Any) -> dict[str, Any]:
    """One cell: every judge's answer about one golden under one model, and the call's summary."""
    row: dict[str, Any] = {
        "model": run.model,
        "golden": run.golden,
        "scores": [
            {
                "metric": score.metric,
                "score": score.score,
                "passed": score.passed,
                # A judgment always carries its reasoning: a hard policy writes the seqs for free,
                # a model is asked for one sentence. `criteria` is the question it answered.
                "reason": score.reason,
                "criteria": score.criteria,
                "judge_calls": score.judge_calls,
            }
            for score in run.scores
        ],
        # `call.summary` verbatim: duration, turns, usage rows and cost. Nothing is recomputed —
        # what the log did not measure, a report never learns.
        "summary": run.summary,
    }
    # Only where something broke: a green golden's prompt is a page nobody opens, and a suite of
    # thirty would carry thirty of them in the row a person reads back. A list is what the model was
    # asked, request by request; null says the run that opened the call kept no requests at all.
    if run.broke:
        row["asked"] = run.asked
    return row


def _a_case(one: Conversation, config: AgentConfig) -> Any:
    """The call's log as a judge reads it, with the contracts the model was shown beside it."""
    return rings.a_case(
        one.entries,
        tools=config.tools_by_name,
        name=one.golden.name,
        knowledge=[config.knowledge.text] if config.knowledge else None,
    )


# A policy that asks nobody and costs nothing has no reason to be opt-in, so consent leads every
# list: rings 3 and 4 already run it over every call, and a golden that names no `expect` used to
# be the one conversation in this runtime nothing judged at all. Every other judge still needs a
# declaration — `register` the register the business asked for, `grounded` the one question it
# may put to a model — and inventing either would grade a rule nobody wrote down.
def _judges_for(golden: Golden, case: Any) -> list[Any]:
    """Consent, which every call carries its own evidence for, then whatever `expect` names."""
    expect = golden.expect
    judges: list[Any] = [rings.ConsentJudge(case.gate)]
    # Beside consent and for the same reason: it asks nobody, it costs nothing, and without it a
    # golden whose caller was never heard reads as held by every expectation written as an absence.
    if golden.input:
        judges.append(rings.TheCallerWasHeardJudge(len(golden.input)))
    if expect.tools:
        judges.append(rings.EveryToolRanJudge(expect.tools))
    if expect.not_tools:
        judges.append(rings.NoForbiddenToolRanJudge(expect.not_tools, case.gate))
    if expect.not_said:
        judges.append(rings.NothingWasSaidJudge(expect.not_said))
    if expect.says:
        judges.append(rings.EveryPhraseWasSaidJudge(expect.says))
    if expect.grounded:
        judges.append(rings.GroundedJudge(rings.EXTRACTORS, rings.evidence_of(case)))
    if expect.addressed_as is not None:
        judges.append(rings.RegisterJudge(expect.addressed_as))
    if expect.replies is not None:
        judges.append(rings.TheEventWasAnsweredJudge(expect.replies, case))
    return judges
