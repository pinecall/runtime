"""Tests for the roster of each fleet."""

from pinecall.fleet.roster import FORGOTTEN_AFTER_S, STALE_AFTER_S, Roster
from pinecall.wire.rest.fleet import HeartbeatRequest

SANDBOX = "pinecall-sandbox"


def beat(
    worker: str, *, active: int = 0, max_jobs: int | None = 4, load: float = 0.1
) -> HeartbeatRequest:
    """A heartbeat of the sandbox's fleet."""
    return HeartbeatRequest(
        fleet=SANDBOX, worker=worker, active=active, max_jobs=max_jobs, load=load, draining=False
    )


def test_a_fleet_nobody_heard_from_is_not_full() -> None:
    assert not Roster().totals(SANDBOX, 0.0).full


def test_the_free_seats_are_what_each_worker_holds_under_its_measure() -> None:
    roster = Roster()
    roster.report(beat("w1", active=1), 0.0)
    roster.report(beat("w2", active=3), 0.0)
    totals = roster.totals(SANDBOX, 1.0)
    assert (totals.workers, totals.active, totals.seats, totals.free) == (2, 4, 8, 4)


def test_a_worker_at_livekits_line_accepts_nothing_and_a_fleet_of_it_is_full() -> None:
    roster = Roster()
    roster.report(beat("w1", load=0.7), 0.0)
    assert roster.totals(SANDBOX, 0.0).full


def test_a_cpu_gated_worker_counts_no_seat_and_still_accepts() -> None:
    roster = Roster()
    roster.report(beat("w1", max_jobs=None), 0.0)
    totals = roster.totals(SANDBOX, 0.0)
    assert (totals.seats, totals.accepting) == (0, 1)


def test_one_fleet_full_leaves_the_other_open() -> None:
    roster = Roster()
    roster.report(beat("w1", load=0.9), 0.0)
    production = HeartbeatRequest(
        fleet="pinecall", worker="w9", active=0, max_jobs=4, load=0.1, draining=False
    )
    roster.report(production, 0.0)
    assert roster.totals(SANDBOX, 0.0).full
    assert not roster.totals("pinecall", 0.0).full


def test_a_silent_worker_is_no_capacity_and_is_forgotten_an_hour_later() -> None:
    roster = Roster()
    roster.report(beat("w1"), 0.0)
    assert roster.totals(SANDBOX, STALE_AFTER_S + 1).workers == 0
    assert [seat.worker for seat in roster.of(SANDBOX, STALE_AFTER_S + 1)] == ["w1"]
    assert roster.of(SANDBOX, FORGOTTEN_AFTER_S + 1) == []


def test_a_cordon_outlives_the_next_heartbeat_and_is_told_back() -> None:
    roster = Roster()
    roster.report(beat("w1"), 0.0)
    assert roster.cordon(SANDBOX, "w1")
    assert roster.report(beat("w1"), 1.0).cordoned
    assert roster.cordon(SANDBOX, "w1", on=False)
    assert not roster.report(beat("w1"), 2.0).cordoned


def test_cordoning_a_name_nobody_has_is_refused() -> None:
    assert not Roster().cordon(SANDBOX, "ghost")
