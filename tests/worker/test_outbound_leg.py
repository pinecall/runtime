"""The leg a job places: what the dispatch told it, and the three ways the far end says no."""

from __future__ import annotations

from typing import Any

import pytest
from livekit.api.twirp_client import SipCallError

from pinecall.worker import outbound_leg

pytestmark = pytest.mark.unit

A_DIAL: dict[str, Any] = {
    "trunk": "ST_out",
    "to": "+34600123456",
    "shown": "+34910000000",
    "max_duration_s": 600,
}


def test_the_dispatch_is_read_as_the_gateway_wrote_it() -> None:
    wanted = outbound_leg.asked_of(A_DIAL)
    assert wanted is not None
    assert (wanted.trunk, wanted.to, wanted.shown, wanted.max_duration_s) == (
        "ST_out",
        "+34600123456",
        "+34910000000",
        600,
    )


# Only the gateway writes a dispatch, so anything else in this key is a room somebody made by
# hand: it is not a dial, and the job says so rather than placing a call it cannot describe.
@pytest.mark.parametrize(
    "said",
    [
        None,
        "not a mapping at all",
        {"to": "+34600123456"},
        {"trunk": "ST_out", "to": 600_123_456, "shown": "+34910000000"},
    ],
)
def test_anything_that_is_not_a_dial_reads_as_none(said: Any) -> None:
    assert outbound_leg.asked_of(said) is None


def test_a_ceiling_nobody_set_is_no_ceiling_rather_than_a_guess() -> None:
    wanted = outbound_leg.asked_of({**A_DIAL, "max_duration_s": None})
    assert wanted is not None and wanted.max_duration_s == 0


# The SIP status is the only thing that tells the three apart, and the protocol already had a word
# for each: they were reserved for this path and had no producer until now.
@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (486, "busy"),
        (603, "busy"),
        (408, "no_answer"),
        (480, "no_answer"),
        (487, "no_answer"),
        (403, "dial_failed"),
        (503, "dial_failed"),
        (None, "dial_failed"),
    ],
)
def test_the_carriers_answer_becomes_the_logs_own_word_for_it(
    code: int | None, reason: str
) -> None:
    metadata = {} if code is None else {"sip_status_code": str(code)}
    refused = SipCallError("unavailable", "the carrier said no", status=503, metadata=metadata)
    assert outbound_leg.how_it_failed(refused) == reason


def test_anything_that_is_not_a_sip_answer_is_a_dial_that_failed() -> None:
    """A trunk that is not there, an address that does not resolve: not busy, and not an answer."""
    assert outbound_leg.how_it_failed(RuntimeError("no such trunk")) == "dial_failed"
