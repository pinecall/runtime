"""The order rule over hand-written traces: the yes is already written when the tool runs."""

import pytest

from pinecall.types import GATE_DEFERRED_ON, GateLine, consent_of

pytestmark = pytest.mark.unit

BOOK = "book_appointment"
LOOK = "find_slots"
CALLER = "sha256:the-caller"
SUPERVISOR = "sha256:the-supervisor"


def a_booking(seq: int, call_id: str = "toolu_1") -> GateLine:
    """The tool the clinic declared irreversible: it takes a slot away from somebody else."""
    return GateLine(
        seq=seq, kind="tool.call", call_id=call_id, tool=BOOK, side_effect="irreversible"
    )


def a_lookup(seq: int) -> GateLine:
    """A tool that only looks at the world, so no gate is owed for it."""
    return GateLine(seq=seq, kind="tool.call", call_id="toolu_9", tool=LOOK, side_effect="read")


def asked(seq: int, call_id: str = "toolu_1", audience: str = CALLER) -> GateLine:
    """The platform asking, which is the half of the gate that names who is being asked."""
    return GateLine(seq=seq, kind="confirm.request", call_id=call_id, tool=BOOK, audience=audience)


def said_yes(seq: int, call_id: str = "toolu_1", audience: str = CALLER) -> GateLine:
    """The grant a tool call needs behind it."""
    return GateLine(seq=seq, kind="confirm.granted", call_id=call_id, tool=BOOK, audience=audience)


def said_no(seq: int, call_id: str = "toolu_1") -> GateLine:
    """The other answer, which is still an answer: the caller was asked and refused."""
    return GateLine(seq=seq, kind="confirm.declined", call_id=call_id, tool=BOOK, audience=CALLER)


def test_a_booking_after_the_yes_is_kept() -> None:
    read = consent_of([asked(1), said_yes(2), a_booking(3)])

    assert read.outcome == "kept"
    assert read.detail == "1 irreversible tool call(s), each after a granted confirmation"


def test_two_bookings_each_with_its_own_yes_are_kept() -> None:
    """A grant is minted per tool call, so two bookings need two: one grant is not a season pass."""
    read = consent_of(
        [
            said_yes(1, "toolu_1"),
            a_booking(2, call_id="toolu_1"),
            said_yes(3, "toolu_2"),
            a_booking(4, call_id="toolu_2"),
        ]
    )

    assert read.outcome == "kept"
    assert read.detail.startswith("2 irreversible tool call(s)")


def test_a_second_booking_riding_the_first_ones_yes_is_broken() -> None:
    read = consent_of(
        [said_yes(1, "toolu_1"), a_booking(2, call_id="toolu_1"), a_booking(3, "toolu_2")]
    )

    assert read.outcome == "broken"
    assert read.detail == f"{BOOK} ran at seq 3 with no confirm.granted before it"


def test_a_booking_that_ran_before_its_yes_names_both_seqs() -> None:
    """The evidence of a break is the pair of seqs, so the late grant is named and not dropped."""
    read = consent_of([asked(1), a_booking(2), said_yes(3)])

    assert read.outcome == "broken"
    assert read.detail == f"{BOOK} ran at seq 2, before its confirm.granted at seq 3"


def test_a_booking_that_ran_after_a_no_says_so_and_not_that_nobody_answered() -> None:
    """A caller who refused is a sharper finding than a caller who was never asked."""
    read = consent_of([asked(1), said_no(2), a_booking(3)])

    assert read.outcome == "broken"
    assert read.detail == f"{BOOK} ran at seq 3, after its confirm.declined at seq 2"


def test_a_grant_minted_for_somebody_else_is_not_the_callers_yes() -> None:
    read = consent_of([asked(1, audience=CALLER), said_yes(2, audience=SUPERVISOR), a_booking(3)])

    assert read.outcome == "broken"
    assert read.detail == (
        f"{BOOK} at seq 3 was confirmed by {SUPERVISOR} and asked of {CALLER} at seq 1"
    )


def test_a_call_where_nothing_irreversible_ran_is_kept_and_counts_what_did() -> None:
    read = consent_of([a_lookup(1)])

    assert read.outcome == "kept"
    assert read.detail == "no irreversible tool ran in this call; 1 tool call(s) did"


def test_an_irreversible_tool_and_no_confirmation_anywhere_reads_ungated() -> None:
    """What this runtime writes today: the gate is deferred, so the gap is not the agent's."""
    read = consent_of([a_booking(1)])

    assert read.outcome == "ungated"
    assert GATE_DEFERRED_ON in read.detail
    assert "1 irreversible tool call(s) ran" in read.detail


def test_a_trace_nobody_declared_a_side_effect_for_is_undeclared_and_never_kept() -> None:
    """Silence about what a tool does must not read as a call that consented to everything."""
    read = consent_of([GateLine(seq=1, kind="tool.call", call_id="toolu_1", tool=BOOK)])

    assert read.outcome == "undeclared"
    assert read.detail.startswith("1 tool call(s) and not one declared side effect")


def test_an_empty_trace_is_kept_because_a_call_with_no_tool_cannot_break_the_rule() -> None:
    read = consent_of([])

    assert read.outcome == "kept"
    assert read.detail == "no irreversible tool ran in this call; 0 tool call(s) did"
