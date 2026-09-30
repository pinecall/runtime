"""Tests for the score: the golden's call.score reads whole, and a judgment keeps its evidence."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.events import EVENTS, event_of
from pinecall.wire.scores import CallScore, Judgment
from tests.wire.golden import golden_entries


def test_the_golden_logs_score_is_a_call_score_with_its_judgment_and_evidence() -> None:
    score = next(entry for entry in golden_entries() if entry.type == "call.score")
    read = event_of(score)
    assert EVENTS["call.score"] is CallScore
    assert isinstance(read, CallScore)
    assert read.passed is False
    assert [judge.name for judge in read.judges] == ["consent"]
    assert read.judges[0].evidence.seqs == [79, 93]
    assert read.written() == score.data


def test_a_judgment_whose_verdict_is_not_a_word_of_the_wire_is_refused() -> None:
    judged = {"name": "consent", "verdict": "fine", "criteria": "c", "reason": "r"}
    with pytest.raises(DeclarationRefused, match="judgment"):
        Judgment.read({**judged, "evidence": {"seqs": []}}, "judgment")
