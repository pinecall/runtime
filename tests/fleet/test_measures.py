"""Tests for a worker's last minute: what its jobs tell it, and what its heartbeat carries."""

import asyncio
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from pinecall.fleet.measures import (
    MOST_KEPT,
    TURNS_FOR_A_P95,
    WINDOW_S,
    JobReport,
    LastMinute,
    listening,
    measures_path,
    reported,
)
from pinecall.process.settings import Settings
from pinecall.wire.rest.calls import BatchedEntry


def entry(kind: str, **data: object) -> BatchedEntry:
    """One of a call's entries as a job's batch carries it."""
    return BatchedEntry.model_validate({"type": kind, "data": data, "ts": time.time()})


def datagram(report: JobReport) -> bytes:
    """A datagram as a job sends it."""
    return report.model_dump_json().encode()


def test_a_minute_counts_its_calls_their_failures_errors_and_first_audio_at_the_p95() -> None:
    minute = LastMinute()
    for waited in (0.5, 0.6, 0.7, 0.8, 2.0):
        minute.heard(datagram(JobReport(first_audio_s=waited)), 10.0)
    minute.heard(datagram(JobReport(error=True)), 10.0)
    minute.heard(datagram(JobReport(ended="caller_hung_up")), 10.0)
    minute.heard(datagram(JobReport(ended="error")), 10.0)
    last = minute.of(11.0)
    assert (last.ended, last.failed, last.errors, last.turns) == (2, 1, 1, 5)
    assert last.first_audio_p95_s == 2.0


def test_too_few_turns_carry_no_p95_and_a_minute_later_nothing_is_left() -> None:
    minute = LastMinute()
    for _ in range(TURNS_FOR_A_P95 - 1):
        minute.heard(datagram(JobReport(first_audio_s=0.9)), 0.0)
    minute.heard(datagram(JobReport(ended="error")), 0.0)
    assert minute.of(1.0).first_audio_p95_s is None
    gone = minute.of(WINDOW_S + 1)
    assert (gone.ended, gone.failed, gone.errors, gone.turns) == (0, 0, 0, 0)


def test_a_datagram_that_does_not_read_is_dropped() -> None:
    minute = LastMinute()
    minute.heard(b"not json", 0.0)
    minute.heard(b'{"first_audio_s": "slow"}', 0.0)
    assert minute.of(0.0).turns == 0


def test_a_minute_keeps_a_bounded_number_of_turns() -> None:
    minute = LastMinute()
    for _ in range(MOST_KEPT + 10):
        minute.heard(datagram(JobReport(first_audio_s=1.0)), 0.0)
    assert minute.of(0.0).turns == MOST_KEPT


def test_two_workers_of_one_machine_hear_on_their_own_sockets() -> None:
    production = Settings.model_validate({"PINECALL_FLEET": "pinecall"})
    sandbox = Settings.model_validate(
        {"PINECALL_FLEET": "pinecall-sandbox", "PINECALL_WORKER_HTTP_PORT": "8182"}
    )
    assert measures_path(production) != measures_path(sandbox)


# The datagrams are in the socket when `reported` returns: the loop reads them on its next turns.
async def _heard_an_end(minute: LastMinute) -> None:
    for _ in range(100):
        if minute.of(asyncio.get_running_loop().time()).ended:
            return
        await asyncio.sleep(0)


# The job's side and the worker's side over a real datagram socket, as two processes use it.
async def test_what_a_calls_batch_says_reaches_its_worker() -> None:
    path = Path(tempfile.gettempdir()) / f"pc-{uuid4().hex[:8]}.measures"
    minute = LastMinute()
    hearing = await listening(path, minute)
    try:
        reported(
            path,
            [
                entry("turn.agent", text="hola", metrics={"e2e_latency": 0.8}),
                entry("custom", name="x", data={}),
                entry("error", code="component_failed", message="x", recoverable=False),
                entry("call.ended", reason="error"),
            ],
        )
        await _heard_an_end(minute)
        last = minute.of(asyncio.get_running_loop().time())
        assert (last.ended, last.failed, last.errors, last.turns) == (1, 1, 1, 1)
    finally:
        hearing.close()
        path.unlink(missing_ok=True)


def test_a_worker_that_is_not_listening_costs_its_job_nothing() -> None:
    nowhere = Path(tempfile.gettempdir()) / f"pc-{uuid4().hex[:8]}.measures"
    reported(nowhere, [entry("call.ended", reason="error")])
