"""Tests for the monitors door's shapes."""

import pytest
from pydantic import ValidationError

from pinecall.wire.rest.monitors import MonitorRequest


def test_a_request_watches_a_known_metric_over_seven_days_unless_said() -> None:
    asked_for = MonitorRequest.model_validate(
        {"name": "slow", "metric": "e2e_median_s", "above": True, "threshold": 2}
    )
    assert (asked_for.window_days, asked_for.agent) == (7, None)
    with pytest.raises(ValidationError):
        MonitorRequest.model_validate({"name": "x", "metric": "p50", "above": True, "threshold": 1})
