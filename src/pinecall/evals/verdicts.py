"""livekit's three verdicts said in ours, and the entries a judgment named read off its sentence."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from livekit.agents.evals import JudgmentResult, Verdict

from pinecall.log.entry import Entry
from pinecall_protocol.defs import ScoreVerdict
from pinecall_protocol.events import Judgment, JudgmentEvidence

# The whole mapping, in one table: their three words and ours. `skipped` is ours alone and is
# never a judgment — it is the row for a judge nobody put the question to. See scoring.md.
AS_OUR_VERDICT: dict[Verdict, ScoreVerdict] = {
    "pass": "held",
    "fail": "broken",
    "maybe": "deferred",
}

# A judgment carries a verdict, a reasoning and the instructions, and nothing else
# (livekit/agents/evals/judge.py:34-57). So the entries a verdict is about are the ones its own
# sentence names — `pinecall/domain/consent.py` writes the seqs into it for exactly this reader —
# and they are lifted out here rather than decided a second time by a second pass over the log.
A_SEQ = re.compile(r"\bseq (\d+)\b")

# The one key an entry carries somebody's own words under: `confirm.granted` holds the caller's
# yes there. A reader of a broken consent then has the sentence without opening the log.
SAID = "said"


def a_judgment(name: str, result: JudgmentResult, entries: Sequence[Entry]) -> Judgment:
    """One judge's answer as the log carries it, with the entries its reason named beside it."""
    return Judgment(
        name=name,
        verdict=AS_OUR_VERDICT[result.verdict],
        criteria=result.instructions,
        reason=result.reasoning,
        evidence=evidence_in(result.reasoning, entries),
    )


# The judge answered from code as far as code goes and then wanted a model, which the call's
# budget did not have. Its own sentence says what it could not settle; the reason says why nobody
# was asked, and the verdict is the fourth word rather than a fail nobody earned.
def nobody_asked(
    name: str, settled: JudgmentResult, why: str, entries: Sequence[Entry]
) -> Judgment:
    """The row for a judge that needed a model and was not given one."""
    return Judgment(
        name=name,
        verdict="skipped",
        criteria=settled.instructions,
        reason=f"{why}: {settled.reasoning}",
        evidence=evidence_in(settled.reasoning, entries),
    )


def evidence_in(reason: str, entries: Sequence[Entry]) -> JudgmentEvidence:
    """The entries this sentence named, and the words the first of them that carries any holds."""
    seqs = list(dict.fromkeys(int(found) for found in A_SEQ.findall(reason)))
    said = next((words for seq in seqs if (words := _said_at(seq, entries)) is not None), None)
    evidence: dict[str, Any] = {"seqs": seqs}
    if said is not None:
        evidence["said"] = said
    return JudgmentEvidence.model_validate(evidence)


def _said_at(seq: int, entries: Sequence[Entry]) -> str | None:
    """The words the entry at this seq carries, when it is an entry that carries any."""
    entry = next((one for one in entries if one.seq == seq), None)
    words = entry.data.get(SAID) if entry is not None else None
    return words if isinstance(words, str) and words else None
