"""`pinecall-runtime fleet list`: the roster and the totals, as a person reads them."""

import io
import time

import httpx
import pytest

from pinecall.cli.fleet.verbs import UNCORDONED, cordon, list_workers
from pinecall.cli.operator import Operator
from pinecall.fleet import STALE_AFTER_S, Heartbeat, Roster

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


# Lifting a cordon on a worker that already went is the case these sentences exist for: a cordon
# is how a machine is retired, the unit does not bring a drained worker back, and `uncordoned`
# alone left an operator with no worker at all while `fleet list` still said `accepting` for
# another thirty seconds (box.pinecall.io, 2026-09-20 — it was this session that did it).
async def test_uncordoning_a_worker_that_already_left_says_so_and_names_the_way_back(
    operator: Operator, fleet: Roster
) -> None:
    fleet.report(
        Heartbeat("pinecall-box", active=0, max_jobs=None, load=0.2, draining=False),
        time.time() - STALE_AFTER_S - 60,
    )
    out = printed()

    assert await cordon("pinecall-box", False, operator, out) == 0

    said = out.getvalue()
    assert "already drained and left" in said
    assert "systemctl start pinecall-worker" in said


async def test_uncordoning_a_worker_that_is_still_there_says_it_takes_calls_again(
    operator: Operator, fleet: Roster
) -> None:
    a_worker(fleet, "pinecall-box", active=0, max_jobs=None)
    out = printed()

    assert await cordon("pinecall-box", False, operator, out) == 0

    assert out.getvalue().strip() == UNCORDONED.format(worker="pinecall-box")


async def test_cordoning_says_the_worker_does_not_come_back_on_its_own(
    operator: Operator, fleet: Roster
) -> None:
    a_worker(fleet, "pinecall-box", active=0, max_jobs=None)
    out = printed()

    assert await cordon("pinecall-box", True, operator, out) == 0

    assert "does not come back on its own" in out.getvalue()
