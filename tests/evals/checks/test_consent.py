"""The door's half of consent: the rule's four words as the four statuses `pinecall eval` prints."""

import pytest

from pinecall.evals.checks.consent import consent
from pinecall.evals.checks.replay import Replayed
from pinecall.types import GATE_DEFERRED_ON
from tests.evals.checks.conftest import IRREVERSIBLE

pytestmark = pytest.mark.unit


def test_a_booking_the_caller_said_yes_to_passes(confirmed: Replayed) -> None:
    """The gate as designed: confirm.request, confirm.granted, and only then the tool.call."""
    verdict = consent(confirmed, IRREVERSIBLE)
    assert verdict.status == "held"
    assert "1 irreversible tool call(s)" in verdict.detail


def test_a_booking_that_ran_before_the_yes_fails_and_names_both_seqs(
    before_the_yes: Replayed,
) -> None:
    """A grant that arrived after the booking is a broken gate, not a deferred one."""
    verdict = consent(before_the_yes, IRREVERSIBLE)
    assert verdict.status == "broken"
    assert verdict.detail == ("book_appointment ran at seq 4, before its confirm.granted at seq 6")


def test_an_irreversible_tool_with_no_confirm_at_all_reads_deferred(no_gate: Replayed) -> None:
    """What a real call from this runtime looks like today: deferred, with the date, never FAIL."""
    verdict = consent(no_gate, IRREVERSIBLE)
    assert verdict.status == "deferred"
    assert GATE_DEFERRED_ON in verdict.detail
    assert "1 irreversible tool call(s)" in verdict.detail


def test_a_call_where_nothing_irreversible_ran_passes(no_gate: Replayed) -> None:
    """The tool that booked is a read here: nothing irreversible ran, so nothing needs a gate."""
    assert consent(no_gate, frozenset()).status == "held"


def test_a_call_whose_agent_declares_nothing_here_is_skipped_and_not_passed(
    no_gate: Replayed,
) -> None:
    """Silence about a tool's side effect must not read as a call that consented to everything."""
    verdict = consent(no_gate, None)
    assert verdict.status == "skipped"
    assert "clinica-norte" in verdict.detail
