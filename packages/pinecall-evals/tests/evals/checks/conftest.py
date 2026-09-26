"""The three consent logs the ring-3 checks are written against: read once each, rebuilt."""

import pytest

from pinecall.evals.checks.replay import Replayed, rebuild
from pinecall_protocol import decode_entries
from pinecall_protocol.envelope import Entry
from pinecall_testkit.pinned_logs import BEFORE_THE_YES, CONFIRMED, LOGS, NO_GATE


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
