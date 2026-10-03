"""Tests for whom a call is offered to: a worker heard lately, with a seat free, the freest."""

from pinecall.gateway.dispatching._chooser import HEARD_WITHIN_S, chosen, free_seats
from pinecall.wire.rest.fleet import WorkerStatus

NOW = 1_000.0


def a_seat(
    worker: str,
    *,
    active: int = 0,
    max_jobs: int | None = 4,
    load: float | None = None,
    heard_ago: float = 1.0,
) -> WorkerStatus:
    """A worker of the fleet as its last heartbeat left it."""
    return WorkerStatus(
        fleet="pinecall",
        worker=worker,
        agent_name=f"pinecall/{worker}",
        active=active,
        max_jobs=max_jobs,
        load=(active / max_jobs if max_jobs else 0.1) if load is None else load,
        draining=False,
        cordoned=False,
        seen_at=NOW - heard_ago,
    )


def test_the_worker_with_the_most_seats_free_is_chosen() -> None:
    seats = [a_seat("a", active=3), a_seat("b", active=1), a_seat("c", active=2)]
    picked = chosen(seats, {}, NOW)
    assert picked is not None
    assert picked.worker == "b"


def test_calls_offered_and_not_yet_opened_count_against_a_worker() -> None:
    seats = [a_seat("a", active=1), a_seat("b", active=2)]
    picked = chosen(seats, {"pinecall/a": 2}, NOW)
    assert picked is not None
    assert picked.worker == "b"
    assert free_seats(seats[0], {"pinecall/a": 3}) == 0


def test_a_worker_unheard_lately_full_or_cordoned_is_never_chosen() -> None:
    seats = [
        a_seat("silent", heard_ago=HEARD_WITHIN_S + 1),
        a_seat("full", active=4),
        a_seat("cordoned").model_copy(update={"cordoned": True}),
    ]
    assert chosen(seats, {}, NOW) is None


def test_the_worker_a_room_was_offered_to_is_not_offered_it_again() -> None:
    seats = [a_seat("a"), a_seat("b", active=3)]
    picked = chosen(seats, {}, NOW, not_these=("pinecall/a",))
    assert picked is not None
    assert picked.worker == "b"
    assert chosen([a_seat("a")], {}, NOW, not_these=("pinecall/a",)) is None


def test_a_worker_on_cpu_takes_one_call_at_a_time_under_livekits_line() -> None:
    idle = a_seat("cpu", max_jobs=None, load=0.3)
    assert free_seats(idle, {}) == 1
    assert free_seats(idle, {"pinecall/cpu": 1}) == 0
    assert free_seats(a_seat("cpu", max_jobs=None, load=0.8), {}) == 0
