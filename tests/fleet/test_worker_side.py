"""Tests for what a worker does beside its calls."""

import logging
import stat
from pathlib import Path

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.settings import Settings
from pinecall.fleet import worker_side
from pinecall.fleet.worker_side import (
    Load,
    livekits_measure,
    otlp_headers,
    recording_path,
    traced_to,
)


def test_slots_are_reported_as_the_fraction_held() -> None:
    load = Load(10)
    assert load.at(3) == pytest.approx(0.3)


def test_the_machine_is_measured_by_livekits_own_calculator() -> None:
    assert callable(livekits_measure())


def test_a_worker_holds_at_least_one_call() -> None:
    with pytest.raises(DeclarationRefused, match="at least one call"):
        Load(0)


def test_crossing_the_line_is_said_once_each_way(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=worker_side.__name__)
    load = Load(10)
    for jobs in (7, 8, 3):
        load.at(jobs)
    said = [record.getMessage() for record in caplog.records]
    assert len([one for one in said if "routes no job" in one]) == 1
    assert len([one for one in said if "routes jobs here again" in one]) == 1


def test_a_call_gets_a_directory_of_its_own_the_recorder_may_write_in(tmp_path: Path) -> None:
    audio = recording_path(tmp_path, "call_1")
    assert audio == tmp_path / "call_1" / "audio.ogg"
    mode = (tmp_path / "call_1").stat().st_mode
    assert mode & stat.S_ISGID
    assert mode & stat.S_IWGRP


def test_headers_are_spelled_as_the_otel_variable_spells_them() -> None:
    assert otlp_headers("a=1, b = 2") == {"a": "1", "b": "2"}
    assert otlp_headers(None) == {}


def test_a_header_with_no_equals_is_refused_naming_the_pair() -> None:
    with pytest.raises(DeclarationRefused, match="nope"):
        otlp_headers("a=1,nope")


def test_a_box_naming_no_endpoint_traces_nothing() -> None:
    assert not traced_to(Settings.model_validate({}))
