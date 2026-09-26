"""Ring 4 over the repo's own golden call: who answered, what they cited, and what it cost."""

from typing import Any, override

import pytest
from livekit.agents.evals import Judge, JudgmentResult
from livekit.agents.llm import LLM, ChatContext

from pinecall.evals import Counted, hangup_score
from pinecall.evals.hangup_score import score_call
from pinecall.settings import Settings
from pinecall.types import AgentConfig, ToolSpec
from pinecall_protocol import decode_entries, encode
from pinecall_protocol.envelope import Entry
from pinecall_protocol.events import Judgment
from pinecall_protocol.fixtures import GOLDEN_LOG

pytestmark = pytest.mark.unit

# What the golden call's own agent declares for the two tools that call uses: `find_slots` looks
# and `book_slot` books. The log says which tools ran and never what they do, so without this
# declaration the consent rule has nothing to look at and answers `undeclared`.
THE_GOLDENS_AGENT = AgentConfig(
    slug="clinica-norte",
    tools=(
        ToolSpec(
            name="find_slots",
            description="Huecos libres de un doctor en un día.",
            parameters={"type": "object"},
            side_effect="read",
        ),
        ToolSpec(
            name="book_slot",
            description="Reserva un hueco de la agenda para un paciente.",
            parameters={"type": "object"},
            side_effect="irreversible",
            confirm="Le reservo el hueco de las {{at}}. ¿Lo confirmo?",
        ),
    ),
)

# The budget every unit test runs under (tests/conftest.py): nothing may ask a model in ring 0.
NO_BUDGET = Settings(world="production", judge_ceiling_eur=0)


@pytest.fixture(scope="module")
def golden() -> list[Entry]:
    """The repo's golden call: the booking that ran before the caller's yes."""
    return decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))


async def test_the_golden_booking_call_scores_consent_broken_at_the_seqs_ring_three_prints(
    golden: list[Entry],
) -> None:
    """The tool ran at 79 and the yes arrived at 93, and the entry says both numbers out loud."""
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    consent = _judged(scored.judges, "consent")
    assert consent.verdict == "broken"
    assert consent.reason == "book_slot ran at seq 79, before its confirm.granted at seq 93"
    assert consent.evidence.seqs == [79, 93]
    assert consent.evidence.said == "Sí, confirmo.", "the caller's own yes, at the later seq"
    assert scored.passed is False


async def test_the_verdict_the_golden_carries_is_the_verdict_the_judges_reach(
    golden: list[Entry],
) -> None:
    """The fixture's own call.score is not a hand-written claim: ring 4 writes exactly it."""
    written = next(entry for entry in golden if entry.type == "call.score")
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    reached = encode(scored)
    reached["judges"] = [row for row in reached["judges"] if row["name"] == "consent"]
    assert reached == written.data


async def test_the_ceiling_at_zero_skips_the_judge_that_would_ask_and_the_policies_answer(
    golden: list[Entry],
) -> None:
    """A judge that needs a model is not guessed at: it is `skipped`, and it says by how much."""
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    grounded = _judged(scored.judges, "grounded")
    assert grounded.verdict == "skipped"
    assert grounded.reason.startswith("judging this call may spend 0.0 EUR on a model")
    assert grounded.criteria, "a skipped judge still says which question went unanswered"
    assert _judged(scored.judges, "consent").verdict == "broken", "a policy costs nothing"
    assert scored.judge_calls == 0


async def test_the_bill_is_absent_and_never_zero_when_no_model_reported_a_token(
    golden: list[Entry],
) -> None:
    """Nobody was asked, so nobody was billed — and an unknown bill is not a bill of zero."""
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    assert scored.judge_cost_eur is None
    assert "judge_cost_eur" not in encode(scored)


async def test_a_judging_that_blew_up_writes_the_reason_into_the_tenants_own_log(
    golden: list[Entry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process log is not shipped. What broke belongs where the rest of the call already is."""
    monkeypatch.setattr(hangup_score, "build_case", _refuses_to_build_a_case)
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    assert scored.judges == [] and scored.passed is None
    assert scored.not_judged == "judging this call failed: the log would not read back"


async def test_a_call_whose_every_judge_failed_is_not_a_call_that_passed(
    golden: list[Entry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A judge that broke answers nothing and is dropped, as livekit's own group drops one."""
    monkeypatch.setattr(hangup_score, "_the_judges_of", _one_judge_that_raises)
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    assert scored.judges == [] and scored.passed is None
    assert scored.not_judged == hangup_score.NOTHING_ANSWERED


async def test_a_judge_that_raises_is_in_the_panel_and_never_among_the_judges(
    golden: list[Entry], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped judge answers nothing, so only the panel says it was ever run over the call."""
    monkeypatch.setattr(hangup_score, "GroundedJudge", _raises_instead("grounded"))
    scored = await score_call(golden, THE_GOLDENS_AGENT, NO_BUDGET)
    assert scored.panel == ["consent", "grounded", "promises"], "all were run, whatever answered"
    assert [row.name for row in scored.judges] == ["consent", "promises"], (
        "the one that raised is not"
    )
    assert scored.passed is False, "read off the survivors, and the panel names the one missing"


async def test_a_call_nobody_declared_a_tool_for_breaks_rather_than_reading_as_a_yes(
    golden: list[Entry],
) -> None:
    """A check that could not look must never read as proof: the reason says how to fix it."""
    undeclared = AgentConfig(slug="clinica-norte")
    consent = _judged((await score_call(golden, undeclared, NO_BUDGET)).judges, "consent")
    assert consent.verdict == "broken"
    assert "not one declared side effect" in consent.reason
    assert consent.evidence.seqs == []


def _judged(judges: list[Judgment], name: str) -> Any:
    """One judge's row, by the name the judge itself declares."""
    return next(row for row in judges if row.name == name)


def _one_judge_that_raises(*_args: Any, **_kwargs: Any) -> list[Judge]:
    """The whole panel of a call, replaced by one judge that cannot answer anything."""
    return [_AJudgeThatRaises()]


def _raises_instead(name: str) -> Any:
    """One member of the real panel, built under its own name and unable to answer anything."""

    def built(*_args: Any, **_kwargs: Any) -> Judge:
        return _AJudgeThatRaises(name)

    return built


def _refuses_to_build_a_case(*_args: Any, **_kwargs: Any) -> Any:
    """A `Case` that cannot be built at all: the failure `a_score` promises never to raise."""
    raise RuntimeError("the log would not read back")


class _AJudgeThatRaises(Judge):
    """One judge that breaks where livekit's group swallows it, so nothing is left to answer."""

    def __init__(self, name: str = "breaks") -> None:
        super().__init__(name=name)

    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: LLM[Any] | None = None,
    ) -> JudgmentResult:
        """Never answers."""
        raise RuntimeError("this judge cannot decide anything")


class _Closing(LLM[Any]):
    """A judge model that only knows whether it was closed."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    @property
    @override
    def model(self) -> str:
        return "closing"

    @override
    def chat(self, **_: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride] — never asked
        raise AssertionError("nothing is asked of this judge")

    @override
    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


async def test_closing_the_counter_closes_the_judge_behind_it() -> None:
    """A hang-up built a judge per call and closed none: a client leaked per judged call."""
    judge = _Closing()
    await Counted(judge).aclose()
    assert judge.closed
