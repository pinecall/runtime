"""The four words a verdict is written in, against livekit's three, and the seqs it cites."""

from typing import get_args

import pytest
from livekit.agents.evals import JudgmentResult, Verdict

from pinecall.evals.livekit_verdicts import AS_OUR_VERDICT, a_judgment, evidence_in
from pinecall_protocol.defs import ScoreVerdict
from pinecall_protocol.envelope import Entry

pytestmark = pytest.mark.unit

A_GRANT = Entry(
    seq=93,
    ts=1786537548.0,
    call="CA_8f4a2c",
    agent="clinica-norte",
    type="confirm.granted",
    ephemeral=False,
    data={"tool": "book_slot", "call_id": "toolu_02", "said": "Sí, confirmo."},
)


def test_every_verdict_livekit_can_return_is_said_in_a_word_of_ours() -> None:
    """Three of theirs map to three of ours, and a fourth verdict would fail this on the day."""
    assert set(AS_OUR_VERDICT) == set(get_args(Verdict))


def test_skipped_is_ours_alone_and_no_judgment_is_ever_written_as_one() -> None:
    """Nobody was asked is not something a judge can answer: only this runtime writes it."""
    assert "skipped" not in set(AS_OUR_VERDICT.values())
    assert set(AS_OUR_VERDICT.values()) | {"skipped"} == set(get_args(ScoreVerdict.__value__))


def test_the_seqs_a_reason_names_become_the_entries_a_reader_opens() -> None:
    """A judgment carries a sentence and nothing else, so the citation is read out of it."""
    reason = "book_slot ran at seq 79, before its confirm.granted at seq 93"
    evidence = evidence_in(reason, [A_GRANT])
    assert evidence.seqs == [79, 93], "in the order the sentence named them, each once"
    assert evidence.said == "Sí, confirmo."


def test_a_reason_that_cites_nothing_carries_no_evidence_and_no_words() -> None:
    """An empty citation is honest; an invented one would be a line nobody can open."""
    evidence = evidence_in("no irreversible tool ran in this call", [A_GRANT])
    assert evidence.seqs == [] and evidence.said is None


def test_a_judgment_keeps_the_question_it_answered_beside_the_answer() -> None:
    """`criteria` is livekit's `instructions`: what was asked, in the field it was hung on."""
    result = JudgmentResult(verdict="fail", reasoning="the tool ran at seq 79")
    result.instructions = "Every irreversible tool call ran after a confirm.granted."
    row = a_judgment("consent", result, [A_GRANT])
    assert (row.name, row.verdict) == ("consent", "broken")
    assert row.criteria == result.instructions and row.reason == result.reasoning
