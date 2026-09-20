"""`pinecall-runtime fleet list`: the roster and the totals, as a person reads them."""

import io
import time

import httpx
import pytest

from pinecall.cli.fleet.verbs import list_workers
from pinecall.cli.operator import Operator
from pinecall.fleet import Heartbeat, Roster

pytestmark = pytest.mark.unit


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    """Where a verb writes, so a test reads the terminal instead of capturing a stream."""
    return io.StringIO()


def a_worker(fleet: Roster, name: str, *, active: int, max_jobs: int | None) -> None:
    """One heartbeat, as a worker sends it every few seconds."""
    fleet.report(
        Heartbeat(name, active=active, max_jobs=max_jobs, load=0.2, draining=False), time.time()
    )


async def test_a_counted_fleet_says_how_many_seats_are_free(
    operator: Operator, fleet: Roster
) -> None:
    a_worker(fleet, "pinecall-worker-1", active=2, max_jobs=4)
    out = printed()
    assert await list_workers(operator, out) == 0
    assert "2 seats free" in out.getvalue()


# `0 seats free` beside `1 accepting` read as a fleet with nothing left, on a box whose worker is
# gated by its CPU and was never given a PINECALL_MAX_JOBS to count (box.pinecall.io, 2026-09-20).
async def test_a_cpu_gated_fleet_says_nobody_counted_the_seats(
    operator: Operator, fleet: Roster
) -> None:
    a_worker(fleet, "pinecall-box", active=1, max_jobs=None)
    out = printed()
    assert await list_workers(operator, out) == 0
    said = out.getvalue()
    assert "seats gated by cpu, uncounted" in said
    assert "seats free" not in said
    assert "1 accepting" in said
    assert "FULL" not in said
