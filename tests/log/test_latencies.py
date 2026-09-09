"""The one latency reader: what it takes off a call's turns, and that both readers hold it."""

from statistics import median

import pytest

from pinecall.log import latencies
from pinecall_protocol import decode_entries
from pinecall_protocol.envelope import Entry
from pinecall_protocol.fixtures import GOLDEN_LOG

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def golden() -> list[Entry]:
    return decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))


def test_the_medians_are_the_five_measures_over_the_turns_that_carried_them(
    golden: list[Entry],
) -> None:
    """Computed from the turns themselves, never from a summary somebody wrote afterwards."""
    rows = {row.name: (row.seconds, row.turns) for row in latencies.medians(golden)}
    assert list(rows) == list(latencies.MEASURES)
    for name in latencies.MEASURES:
        carried = [
            entry.data["metrics"][name]
            for entry in golden
            if entry.type in latencies.TURN_TYPES and name in entry.data["metrics"]
        ]
        assert rows[name] == (median(carried), len(carried))
    assert rows["e2e_latency"] == (0.94, 5)


def test_a_measure_no_turn_carried_gets_no_row_at_all(golden: list[Entry]) -> None:
    """Absent is absent: a zero would read as an instant answer, which is a lie about the call."""
    user_turns = [entry for entry in golden if entry.type == "turn.user"]
    assert [row.name for row in latencies.medians(user_turns)] == [
        "transcription_delay",
        "end_of_turn_delay",
    ]


def test_a_sample_keeps_every_value_in_turn_order_and_drops_the_measures_nobody_took(
    golden: list[Entry],
) -> None:
    """The verdict needs the values, not the rows: same read, one step earlier."""
    taken = latencies.samples(golden)
    assert set(taken) <= set(latencies.MEASURES)
    assert len(taken["e2e_latency"]) == 5
    assert latencies.samples([]) == {}
