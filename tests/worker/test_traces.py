"""Tests for a call's traces over OTLP."""

from pinecall.process.settings import Settings
from pinecall.worker._traces import traced_to


def test_a_box_naming_no_endpoint_traces_nothing() -> None:
    assert not traced_to(Settings.model_validate({}))
