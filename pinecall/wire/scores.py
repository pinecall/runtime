"""What the judges said about a call: call.score, one judgment per judge, and its evidence."""

from pydantic import AliasChoices, Field

from pinecall.wire.frames import WireModel
from pinecall.wire.parts import ScoreVerdict


class JudgmentEvidence(WireModel):
    """Where in the call's own log a judgment is about."""

    seqs: list[int]
    said: str | None = None


class Judgment(WireModel):
    """One judge's answer about one call."""

    name: str
    verdict: ScoreVerdict
    criteria: str
    reason: str
    evidence: JudgmentEvidence


class JudgedBy(WireModel):
    """Who gave a score: the judge model, and one hash of every question the panel asked."""

    # None: the panel was settled by code alone, no model asked.
    provider: str | None
    model: str | None
    criteria: str


class CallScore(WireModel):
    """The last entry of a call: what the judges said about it at hang-up, one row per judge."""

    passed: bool | None = None
    not_judged: str | None = None
    judges: list[Judgment]
    panel: list[str] | None = None
    judge_calls: int
    # A score written before money in dollars said it in euros: the same number, read as dollars.
    judge_cost_usd: float | None = Field(
        default=None, validation_alias=AliasChoices("judge_cost_usd", "judge_cost_eur")
    )
    # Absent on a score written before 10.6, or with nothing judged: drift tells a worse agent from
    # a changed judge by it.
    judged_by: JudgedBy | None = None
