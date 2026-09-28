"""Tests for what a worker does beside its calls."""

import logging

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.fleet import heartbeat
from pinecall.fleet.heartbeat import Load, livekits_measure
from pinecall.worker._traces import otlp_headers


def test_slots_are_reported_as_the_fraction_held() -> None:
    load = Load(10)
    assert load.at(3) == pytest.approx(0.3)


def test_the_machine_is_measured_by_livekits_own_calculator() -> None:
    assert callable(livekits_measure())


def test_a_worker_holds_at_least_one_call() -> None:
    with pytest.raises(DeclarationRefused, match="at least one call"):
        Load(0)


def test_crossing_the_line_is_said_once_each_way(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=heartbeat.__name__)
    load = Load(10)
    for jobs in (7, 8, 3):
        load.at(jobs)
    data = [record.getMessage() for record in caplog.records]
    assert len([item for item in data if "routes no job" in item]) == 1
    assert len([item for item in data if "routes jobs here again" in item]) == 1


def test_headers_are_spelled_as_the_otel_variable_spells_them() -> None:
    assert otlp_headers("a=1, b = 2") == {"a": "1", "b": "2"}
    assert otlp_headers(None) == {}


def test_a_header_with_no_equals_is_refused_naming_the_pair() -> None:
    with pytest.raises(DeclarationRefused, match="nope"):
        otlp_headers("a=1,nope")
