"""The load a worker reports, and the one line an operator gets when livekit stops routing to it."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from livekit.agents import AgentServer, JobContext, WorkerOptions

from pinecall._settings import load_settings
from pinecall.worker import main
from pinecall.worker.load import (
    NO_LOAD,
    REFUSED_AT,
    MachineLoad,
    livekits_own_measure,
    reports_no_load,
)

pytestmark = pytest.mark.unit


def test_the_line_is_the_one_livekit_server_refuses_at() -> None:
    """pkg/agent/config.go: `const DefaultTargetLoad = 0.7`, and affinity is 0 at or over it."""
    assert REFUSED_AT == 0.7
    assert NO_LOAD < REFUSED_AT


def test_a_worker_off_the_machines_gate_reports_none_at_all() -> None:
    assert reports_no_load() == NO_LOAD


# Read off the public option, never off livekit's private calculator: the day livekit renames the
# option this fails to import, and the day it renames the class nothing of ours notices.
def test_the_machine_is_measured_by_the_calculator_livekit_itself_defaults_to() -> None:
    assert livekits_own_measure() is WorkerOptions(entrypoint_fnc=_never_called).load_fnc


def test_the_machines_load_is_reported_as_livekit_measured_it(a_server: AgentServer) -> None:
    """We wrap livekit's calculator to watch it, and hand back exactly what it said."""
    assert MachineLoad(measuring=lambda _: 0.42)(a_server) == 0.42


# The regression of 2026-09-07: a worker at 0.99 was registered, healthy and never sent a job, and
# neither log said why. This line is the whole difference between that and a five-second diagnosis.
def test_crossing_the_line_warns_once_naming_the_number_and_what_it_costs(
    a_server: AgentServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    climbing = _readings(0.42, 0.71, 0.99)
    watched = MachineLoad(measuring=lambda _: next(climbing))
    for _ in range(3):
        watched(a_server)
    said = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(said) == 1
    assert "0.71" in said[0].getMessage()
    assert "route no job" in said[0].getMessage()


def test_falling_back_under_the_line_says_the_worker_is_reachable_again(
    a_server: AgentServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    falling = _readings(0.99, 0.42, 0.43)
    watched = MachineLoad(measuring=lambda _: next(falling))
    for _ in range(3):
        watched(a_server)
    said = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert len(said) == 1
    assert "0.42" in said[0] and "routing jobs here again" in said[0]


@pytest.fixture
def a_server() -> AgentServer:
    """The AgentServer livekit passes to a load_fnc, built exactly as the process builds it."""
    return main.a_server(load_settings())


def _readings(*loads: float) -> Iterator[float]:
    """The machine, one tick at a time, so a crossing is a fact of the test and not of the box."""
    return iter(loads)


async def _never_called(ctx: JobContext) -> None:
    """An entrypoint WorkerOptions insists on, only ever built so its defaults can be read."""
    raise AssertionError(ctx)
