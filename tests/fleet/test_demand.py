"""Tests for what a cluster's fleet asks Kubernetes for: the scaled workers its calls need."""

from pinecall.fleet.demand import Line, wanted_scaled
from pinecall.wire.rest.fleet import WorkerStatus

NOW = 1_000.0

SCALED = "worker-burst-"


def a_seat(
    worker: str,
    *,
    active: int = 0,
    max_jobs: int | None = 4,
    seen_ago: float = 1.0,
    cordoned: bool = False,
) -> WorkerStatus:
    """A worker as its last heartbeat left it."""
    return WorkerStatus(
        fleet="pinecall",
        worker=worker,
        active=active,
        max_jobs=max_jobs,
        load=active / (max_jobs or 1),
        draining=False,
        cordoned=cordoned,
        seen_at=NOW - seen_ago,
    )


def test_calls_the_core_holds_ask_for_no_scaled_worker() -> None:
    core = [a_seat("worker-core-a", active=1, max_jobs=2), a_seat("worker-core-b", max_jobs=2)]
    line = Line(seats_per_worker=32)
    assert wanted_scaled(core, SCALED, line, NOW) == (0, 1, 4)


def test_a_busy_core_asks_for_the_scaled_workers_its_calls_need() -> None:
    core = [
        a_seat("worker-core-a", active=2, max_jobs=2),
        a_seat("worker-core-b", active=2, max_jobs=2),
    ]
    line = Line(seats_per_worker=32)
    assert wanted_scaled(core, SCALED, line, NOW) == (1, 4, 4)
    many = [*core, a_seat(f"{SCALED}1", active=30, max_jobs=32)]
    assert wanted_scaled(many, SCALED, line, NOW)[0] == 2


def test_the_most_cuts_what_is_asked_however_busy() -> None:
    core = [a_seat("worker-core-a", active=2, max_jobs=2)]
    busy = [*core, *(a_seat(f"{SCALED}{n}", active=32, max_jobs=32) for n in range(3))]
    assert wanted_scaled(busy, SCALED, Line(at_most=3, seats_per_worker=32), NOW)[0] == 3


def test_the_decimal_target_is_read_as_written_not_as_a_float() -> None:
    # 6 held on no seat of its own: 6 / 0.6 is 10 seats, one worker of 10; in floats, 11 seats.
    core = [a_seat("worker-core-a", active=6, max_jobs=0)]
    assert wanted_scaled(core, SCALED, Line(seats_per_worker=10), NOW)[0] == 1


def test_a_quiet_fleet_lets_one_go_and_holds_inside_the_slack() -> None:
    line = Line(seats_per_worker=32)
    two = [a_seat(f"{SCALED}1", active=1, max_jobs=32), a_seat(f"{SCALED}2", max_jobs=32)]
    assert wanted_scaled(two, SCALED, line, NOW)[0] == 1
    # 16 calls on one of 32 is 0.5: under the target, over target - slack, so the one stays.
    half = [a_seat(f"{SCALED}1", active=16, max_jobs=32)]
    assert wanted_scaled(half, SCALED, line, NOW)[0] == 1
    assert wanted_scaled([a_seat(f"{SCALED}1", max_jobs=32)], SCALED, line, NOW)[0] == 0


def test_a_silent_or_cordoned_worker_holds_no_seat() -> None:
    gone = a_seat(f"{SCALED}1", max_jobs=32, seen_ago=600.0)
    cordoned = a_seat(f"{SCALED}2", active=1, max_jobs=32, cordoned=True)
    assert wanted_scaled([gone, cordoned], SCALED, Line(seats_per_worker=32), NOW) == (1, 1, 0)
