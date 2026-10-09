"""Tests for a monitor: what it watches, the line, and which side is the wrong one."""

from typing import Any

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.monitor import Monitor


def test_a_ceiling_fires_above_it_and_a_floor_below_it() -> None:
    slow = Monitor(
        "mon_1", "slow answers", "e2e_median_s", above=True, threshold=2.0, window_days=7
    )
    assert slow.crossed(2.5)
    assert not slow.crossed(2.0)
    judged = Monitor("mon_2", "judges", "held_rate", above=False, threshold=0.9, window_days=1)
    assert judged.crossed(0.85)
    assert not judged.crossed(0.9)


def test_a_metric_a_window_a_name_and_a_share_are_checked() -> None:
    # As a row reads it: the metric is whatever the column holds.
    read: dict[str, Any] = {"id": "mon_3", "name": "x", "metric": "p50", "above": True}
    with pytest.raises(DeclarationRefused, match="watches one of"):
        Monitor(**read, threshold=1.0, window_days=7)
    with pytest.raises(DeclarationRefused, match="1, 7 or 30"):
        Monitor("mon_3", "x", "calls", above=True, threshold=1.0, window_days=3)
    with pytest.raises(DeclarationRefused, match="has a name"):
        Monitor("mon_3", " ", "calls", above=True, threshold=1.0, window_days=7)
    with pytest.raises(DeclarationRefused, match="share between 0 and 1"):
        Monitor("mon_3", "x", "held_rate", above=False, threshold=90, window_days=7)
