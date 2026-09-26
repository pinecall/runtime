"""The golden log reduces to the golden state here, in ring 0, and resumes from any cut of it."""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pinecall.log.reduce import apply, reduce
from pinecall_protocol import decode_entries, decode_entry, encode
from pinecall_protocol.envelope import Entry
from pinecall_protocol.fixtures import GOLDEN_LOG, GOLDEN_STATE
from pinecall_testkit.ring0 import SEARCH_S

pytestmark = pytest.mark.unit

GOLDEN = decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))
EXPECTED = json.loads(GOLDEN_STATE.read_text(encoding="utf-8"))

# Every place the log can be cut, the last one being "after everything".
A_CUT = st.integers(min_value=0, max_value=len(GOLDEN))
# Every place a late reader can come in at: before an entry it will then read.
A_RESUME = st.integers(min_value=0, max_value=len(GOLDEN) - 1)


# The parity contract with the TypeScript reducer, whole: not four keys of the state but all of
# it, so a field one side adds and the other forgets fails here before it fails in a console.
def test_the_golden_log_reduces_to_the_golden_state_whole() -> None:
    assert encode(reduce(GOLDEN)) == EXPECTED


def test_the_golden_carries_both_kinds_of_entry() -> None:
    """The Postgres round trip is only worth anything if the fixture has ephemerals in it."""
    assert any(entry.ephemeral for entry in GOLDEN)
    assert any(not entry.ephemeral for entry in GOLDEN)


@pytest.mark.timeout(SEARCH_S)
@settings(max_examples=len(GOLDEN) + 1, deadline=None)
@given(cut=A_CUT)
def test_a_state_kept_at_any_cut_folds_the_rest_to_the_same_state(cut: int) -> None:
    """A reader that reduced up to here and goes on entry by entry ends where a fresh one does."""
    state = reduce(GOLDEN[:cut])
    for entry in GOLDEN[cut:]:
        state = apply(state, entry)
    assert encode(state) == EXPECTED


@pytest.mark.timeout(SEARCH_S)
@settings(max_examples=len(GOLDEN), deadline=None)
@given(cut=A_RESUME)
def test_a_gap_carrying_a_snapshot_of_any_cut_resumes_to_the_same_state(cut: int) -> None:
    """A late reader given a snapshot and the tail lands on the whole, and remembers the gap."""
    resumed = encode(reduce([_a_gap_at(cut), *GOLDEN[cut:]]))
    ours = {"from_seq": 1, "to_seq": cut}
    assert resumed["gaps"].count(ours) == 1
    assert [gap for gap in resumed["gaps"] if gap != ours] == EXPECTED["gaps"]
    assert {key: value for key, value in resumed.items() if key != "gaps"} == {
        key: value for key, value in EXPECTED.items() if key != "gaps"
    }


def _a_gap_at(cut: int) -> Entry:
    """The log.gap a reader gets for the first `cut` entries: their state, and where they ended."""
    at = GOLDEN[cut]
    return decode_entry(
        {
            "seq": at.seq,
            "ts": at.ts,
            "agent": at.agent,
            "call": at.call,
            "type": "log.gap",
            "ephemeral": False,
            "data": {"from_seq": 1, "to_seq": cut, "snapshot": encode(reduce(GOLDEN[:cut]))},
        }
    )
