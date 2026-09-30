"""What the judges said about a call: call.score, one judgment per judge, and its evidence."""

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


class CallScore(WireModel):
    """The last entry of a call: what the judges said about it at hang-up, one row per judge."""

    passed: bool | None = None
    not_judged: str | None = None
    judges: list[Judgment]
    panel: list[str] | None = None
    judge_calls: int
    judge_cost_usd: float | None = None
