"""The three consent logs the ring-3 checks are written against: read once each, rebuilt."""

from pathlib import Path

import pytest

from pinecall.evals.checks.replayed import Replayed, rebuild
from pinecall_protocol import decode_entries
from pinecall_protocol.envelope import Entry

# Not goldens: no reducer on either side is judged by them, so they live beside the tests that
# read them rather than in protocol/fixtures/, which is the pair both languages must agree on.
LOGS = Path(__file__).parent.parent / "logs"

CONFIRMED = "booking-confirmed.json"
BEFORE_THE_YES = "booking-before-the-yes.json"
NO_GATE = "booking-with-no-gate.json"

# What the clinic declared irreversible: the one tool that takes a slot away from somebody else.
IRREVERSIBLE = frozenset({"book_appointment"})


def entries_of(name: str) -> list[Entry]:
    """One of the three logs, as the store would have handed it back."""
    return decode_entries((LOGS / name).read_text(encoding="utf-8"))


@pytest.fixture
def confirmed() -> Replayed:
    """The call the gate was made for: asked, granted, then booked."""
    return rebuild(entries_of(CONFIRMED))


@pytest.fixture
def before_the_yes() -> Replayed:
    """A broken gate: the booking ran while the caller was still being asked."""
    return rebuild(entries_of(BEFORE_THE_YES))


@pytest.fixture
def no_gate() -> Replayed:
    """What this runtime writes today: an irreversible tool ran and nothing asked anybody."""
    return rebuild(entries_of(NO_GATE))
