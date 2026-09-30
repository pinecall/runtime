"""Tests for what a worker does beside its calls."""

import asyncio
import logging
import socket

import pytest
from livekit.agents import AgentServer

from pinecall.domain.errors import DeclarationRefused
from pinecall.fleet import heartbeat
from pinecall.fleet.client import gateway_at
from pinecall.fleet.heartbeat import Heartbeats, Load, livekits_measure, worker_name_of
from pinecall.fleet.measures import JobReport, LastMinute
from pinecall.process.settings import Settings
from pinecall.worker._traces import otlp_headers
from tests.fakes.livekit import A_SECRET


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


def test_a_worker_is_named_by_its_setting_or_else_its_short_host() -> None:
    named = Settings.model_validate({"PINECALL_WORKER_NAME": "pinecall-worker-7"})
    assert worker_name_of(named) == "pinecall-worker-7"
    assert worker_name_of(Settings.model_validate({})) == socket.gethostname().split(".")[0]


def _no_jobs(_server: AgentServer) -> list[object]:
    return []


async def test_a_beat_carries_what_the_workers_calls_did_in_its_last_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A server that never ran has no process pool to count the jobs of.
    monkeypatch.setattr(AgentServer, "active_jobs", property(_no_jobs))
    settings = Settings.model_validate({"PINECALL_WORKER_NAME": "w-7", "PINECALL_MAX_JOBS": "4"})
    server = AgentServer(ws_url="ws://127.0.0.1:7880", api_key="APIfake", api_secret=A_SECRET)
    minute = LastMinute()
    now = asyncio.get_running_loop().time()
    for waited in (0.4, 0.5, 0.6, 0.7, 3.0):
        minute.heard(JobReport(first_audio_s=waited).model_dump_json().encode(), now)
    minute.heard(JobReport(ended="error").model_dump_json().encode(), now)
    gateway = gateway_at("http://127.0.0.1:9", None)
    beat = Heartbeats(server, gateway, settings, minute).beat()
    await gateway.aclose()
    assert (beat.worker, beat.ended, beat.failed, beat.errors, beat.turns) == ("w-7", 1, 1, 0, 5)
    assert beat.first_audio_p95_s == 3.0
