"""The other three code checks over the same rebuilt calls: the words, the errors, the latencies."""

import dataclasses

import pytest

from pinecall.evals.checks.latency import DEFAULT_BUDGET, latency
from pinecall.evals.checks.provider_errors import errors
from pinecall.evals.checks.register import register
from pinecall.evals.checks.replay import Failure, Replayed, rebuild
from tests.evals.checks.conftest import BEFORE_THE_YES, entries_of

pytestmark = pytest.mark.unit

# What a clinic that speaks to its patients de usted will not have its agent say.
TUTEO = ("te", "tienes", "tu cita")


def test_the_register_scan_names_the_word_and_the_turn_it_was_said_in() -> None:
    """`Ya te la reservé` is the agent tutear-ing a patient the clinic addresses de usted."""
    verdict = register(rebuild(entries_of(BEFORE_THE_YES)), TUTEO)
    assert verdict.status == "broken"
    assert verdict.detail == "the agent said 'te' in turn 1"


def test_the_register_scan_passes_a_call_that_said_none_of_the_words(confirmed: Replayed) -> None:
    """The same three words over a call that kept the register: passed, and it says how many."""
    verdict = register(confirmed, TUTEO)
    assert verdict.status == "held"
    assert "3 declared word(s)" in verdict.detail


def test_a_scan_with_no_declared_words_is_skipped_and_not_a_pass(confirmed: Replayed) -> None:
    """Nobody declared anything, so nothing was checked: saying `passed` would be a lie."""
    assert register(confirmed, ()).status == "skipped"


def test_a_call_that_logged_nothing_broken_passes_the_error_check(confirmed: Replayed) -> None:
    """The happy path of a provider: no `error` entry at all in the whole log."""
    assert errors(confirmed).status == "held"


def test_an_error_the_session_did_not_recover_from_fails_and_names_it(confirmed: Replayed) -> None:
    """A provider that fell over mid-call is the call's verdict, not a line somebody may miss."""
    broke = Failure(seq=6, code="tts_unavailable", message="elevenlabs: 502", recoverable=False)
    verdict = errors(dataclasses.replace(confirmed, failures=(broke,)))
    assert verdict.status == "broken"
    assert verdict.detail == "seq 6 tts_unavailable: elevenlabs: 502"


def test_an_error_the_session_recovered_from_is_named_and_still_passes(confirmed: Replayed) -> None:
    """A retried request is not a failed call, and hiding it would make the retry invisible."""
    hiccup = Failure(
        seq=6, code="stt_reconnected", message="soniox: socket reopened", recoverable=True
    )
    verdict = errors(dataclasses.replace(confirmed, failures=(hiccup,)))
    assert verdict.status == "held"
    assert "recovered from 1 error(s)" in verdict.detail


def test_the_latency_verdict_is_the_median_of_the_turns_against_the_budget(
    confirmed: Replayed,
) -> None:
    """Two agent turns, so each median is the pair's average, judged under livekit's own names."""
    verdict = latency(confirmed, DEFAULT_BUDGET)
    assert verdict.status == "held"
    assert "e2e_latency 1.040s <= 2.000s over 2 turns" in verdict.detail
    assert "llm_node_ttft 0.400s" in verdict.detail
    assert "tts_node_ttfb 0.200s" in verdict.detail


def test_a_budget_the_call_did_not_keep_fails(confirmed: Replayed) -> None:
    """The same call under a budget half a second tighter: the median is over, and it says so."""
    verdict = latency(confirmed, {"e2e_latency": 0.5})
    assert verdict.status == "broken"
    assert verdict.detail == "e2e_latency 1.040s > 0.500s over 2 turns"


def test_a_call_whose_turns_measured_nothing_is_skipped(confirmed: Replayed) -> None:
    """A text session that timed no turn has no latency to judge, and zero would read as instant."""
    verdict = latency(dataclasses.replace(confirmed, latencies={}), DEFAULT_BUDGET)
    assert verdict.status == "skipped"
    assert "e2e_latency" in verdict.detail
