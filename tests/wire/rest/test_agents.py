"""Tests for the bodies of the agent doors."""

from pinecall.wire.rest.agents import JudgingSettings


def test_judging_off_says_no_ceiling_as_null() -> None:
    assert JudgingSettings(on=False, ceiling_usd=None).written() == {
        "on": False,
        "ceiling_usd": None,
    }
