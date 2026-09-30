"""Tests for the histogram: times counted in buckets, added, and read back within 5 %."""

import pytest

from pinecall.log import _histogram as histogram


def test_a_rank_read_off_the_buckets_is_within_five_percent_of_the_true_one() -> None:
    values = [0.05 + 3.95 * ((step * 0.618) % 1.0) for step in range(1001)]
    values.sort()
    counted = histogram.counted(values)
    median = histogram.rank(counted, histogram.MEDIAN)
    p95 = histogram.rank(counted, histogram.P95)
    assert median is not None
    assert p95 is not None
    assert median == pytest.approx(values[500], rel=0.05)
    assert p95 == pytest.approx(values[950], rel=0.05)


def test_two_histograms_added_are_the_histogram_of_both_calls() -> None:
    first, second = [0.2, 0.4], [1.0, 1.2, 9.0]
    both = histogram.added(histogram.counted(first), histogram.counted(second))
    assert both == histogram.counted([*first, *second])
    assert sum(both) == 5
    assert histogram.rank(both, histogram.MEDIAN) == pytest.approx(1.0, rel=0.05)


def test_the_edges_hold_what_is_faster_and_slower_than_the_buckets_and_skip_none() -> None:
    counted = histogram.counted([0.001, -1.0, 600.0])
    assert sum(counted) == 2, "livekit's -1 says no value and is not a time"
    assert counted[0] == 1
    assert counted[-1] == 1
    assert histogram.rank(counted, histogram.MEDIAN) == histogram.SMALLEST_S / 2
    assert histogram.rank(counted, 1.0) == histogram.UPPER[-1]
    assert len(counted) == histogram.BUCKETS


def test_a_histogram_that_counted_nothing_has_no_rank() -> None:
    assert histogram.rank(histogram.empty(), histogram.MEDIAN) is None
