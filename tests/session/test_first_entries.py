"""The first entries of a call: which door it came through, which run opened it, which world."""

from dataclasses import replace
from datetime import date

import pytest

from pinecall.session.first_entries import arrived, started
from pinecall.types import PRODUCTION, SANDBOX, CallContext, Env, Route

pytestmark = pytest.mark.unit

A_NUMBER = "+34910000000"


def a_context(env: Env, direction: str = "inbound") -> CallContext:
    """One phone call on the clinic's number, in the world the test names."""
    return CallContext(
        call="call_first",
        channel="phone",
        direction=direction,  # pyright: ignore[reportArgumentType] — a test names it as a word
        caller="+34600123456",
        route=Route(
            org="clinica", agent="clinica-norte", channel="phone", number=A_NUMBER, env=env
        ),
        today=date(2026, 9, 12),
    )


def test_call_started_says_which_world_the_call_ran_in() -> None:
    """The world is the door's, so a call opened on a development route is filed under it."""
    written = started(a_context(SANDBOX), A_NUMBER, 1.5)
    assert written.env == SANDBOX
    assert (written.channel, written.direction, written.from_, written.to) == (
        "phone",
        "inbound",
        "+34600123456",
        A_NUMBER,
    )
    assert started(a_context(PRODUCTION), A_NUMBER, 1.5).env == PRODUCTION


def test_the_arrival_is_ringing_for_an_inbound_call_and_dialing_for_an_outbound_one() -> None:
    ringing, offered = arrived(a_context(PRODUCTION), A_NUMBER)
    dialing, placed = arrived(a_context(PRODUCTION, "outbound"), A_NUMBER, asked_by="m_ana")
    assert (ringing, dialing) == ("call.ringing", "call.dialing")
    assert offered.model_dump()["route"]["number"] == A_NUMBER
    assert placed.model_dump()["asked_by"] == "m_ana"


# The two ends swap with the direction, and nothing else about the entry does. A ring came FROM
# the caller and went TO our door; a call we placed went the other way, and writing it the first
# way put our own number in the `to` of every outbound log and the customer's in the `from`.
def test_the_two_ends_swap_with_the_direction() -> None:
    _, offered = arrived(a_context(PRODUCTION), A_NUMBER)
    _, placed = arrived(a_context(PRODUCTION, "outbound"), A_NUMBER, asked_by="m_ana")
    rang = offered.model_dump(by_alias=True)
    assert (rang["from"], rang["to"]) == ("+34600123456", A_NUMBER)
    dialled = placed.model_dump(by_alias=True)
    assert (dialled["from"], dialled["to"]) == (A_NUMBER, "+34600123456")
    said = started(a_context(PRODUCTION, "outbound"), A_NUMBER, 1.5).model_dump(by_alias=True)
    assert (said["from"], said["to"], said["direction"]) == (A_NUMBER, "+34600123456", "outbound")


# A ring is asked for by nobody: the field exists for the one direction somebody pressed a button.
def test_only_a_call_we_placed_says_who_asked_for_it() -> None:
    _, offered = arrived(a_context(PRODUCTION), A_NUMBER)
    assert "asked_by" not in offered.model_dump()


# The `persona` judge reads the caller's rule off THIS entry at hang-up, so a call is judged by what
# the caller was when it was made — and a person's call carries none, and gets no such judge.
def test_call_started_carries_the_callers_own_rule_and_a_persons_call_carries_none() -> None:
    ruled = replace(
        a_context(PRODUCTION), persona="homeowner", accepts_when="a price", declines_when="later"
    )
    written = started(ruled, A_NUMBER, 1.5)
    assert (written.accepts_when, written.declines_when) == ("a price", "later")
    nobody = started(a_context(PRODUCTION), A_NUMBER, 1.5)
    assert (nobody.accepts_when, nobody.declines_when) == (None, None)
