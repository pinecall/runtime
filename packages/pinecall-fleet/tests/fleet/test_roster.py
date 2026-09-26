"""Heartbeats into the roster: who is up, who accepts, when it is full, what a cordon does."""

from __future__ import annotations

import pytest

from pinecall.fleet import FORGOTTEN_AFTER_S, REFUSED_AT, STALE_AFTER_S, Heartbeat, Roster

pytestmark = pytest.mark.unit

NOW = 1_000.0


def beat(
    worker: str, active: int = 0, max_jobs: int | None = 4, load: float | None = None
) -> Heartbeat:
    """One heartbeat as a seat worker sends it: the load is active over max unless said."""
    reported = load if load is not None else (active / max_jobs if max_jobs else 0.0)
    return Heartbeat(worker=worker, active=active, max_jobs=max_jobs, load=reported, draining=False)


def test_the_line_is_the_one_livekit_server_refuses_at() -> None:
    """pkg/agent/config.go: `const DefaultTargetLoad = 0.7`; the gate and the roster share it."""
    assert REFUSED_AT == 0.7


def test_an_empty_roster_is_not_full_because_nobody_is_there_to_refuse() -> None:
    """A gateway no worker has knocked at is the world before the fleet: it refuses nothing."""
    totals = Roster().totals(NOW)
    assert (totals.workers, totals.full) == (0, False)


def test_the_fleets_free_seats_are_the_sum_over_workers_of_max_minus_active() -> None:
    roster = Roster()
    roster.report(beat("w1", active=1, max_jobs=4), NOW)
    roster.report(beat("w2", active=3, max_jobs=5), NOW)
    totals = roster.totals(NOW)
    assert (totals.workers, totals.active, totals.seats, totals.free) == (2, 4, 9, 5)
    assert totals.busy == pytest.approx(4 / 9)


def test_a_worker_at_or_over_the_line_accepts_nothing_even_with_a_seat_free() -> None:
    """3 of 4 is 0.75: livekit routes nothing there, and the tolerance seat is not a free seat."""
    roster = Roster()
    roster.report(beat("w1", active=3, max_jobs=4), NOW)
    totals = roster.totals(NOW)
    assert (totals.free, totals.accepting, totals.full) == (1, 0, True)


def test_a_cpu_gated_worker_counts_no_seats_but_still_accepts_under_the_line() -> None:
    """The hub's own worker on a full box: no MAX_JOBS, a CPU number, and it takes calls."""
    roster = Roster()
    roster.report(beat("hub", active=2, max_jobs=None, load=0.4), NOW)
    totals = roster.totals(NOW)
    assert (totals.seats, totals.free, totals.accepting, totals.full) == (0, 0, 1, False)


def test_a_worker_not_heard_from_in_thirty_seconds_is_no_longer_counted() -> None:
    roster = Roster()
    roster.report(beat("w1"), NOW)
    assert roster.totals(NOW + STALE_AFTER_S).workers == 1
    assert roster.totals(NOW + STALE_AFTER_S + 1).workers == 0
    # The seat stays in the listing, so a person sees WHEN it went quiet.
    assert [seat.worker for seat in roster.seats()] == ["w1"]


def test_a_cordon_survives_the_next_heartbeat_and_is_told_back() -> None:
    roster = Roster()
    assert roster.report(beat("w1"), NOW).cordoned is False
    assert roster.cordon("w1") is True
    standing = roster.report(beat("w1", active=1), NOW + 5)
    assert standing.cordoned is True
    assert roster.seats()[0].cordoned is True


def test_a_cordoned_worker_accepts_nothing_and_a_fleet_of_only_it_is_full() -> None:
    roster = Roster()
    roster.report(beat("w1"), NOW)
    roster.cordon("w1")
    assert roster.report(beat("w1"), NOW).full is True


def test_a_cordon_can_be_taken_back() -> None:
    roster = Roster()
    roster.report(beat("w1"), NOW)
    roster.cordon("w1")
    assert roster.cordon("w1", cordoned=False) is True
    assert roster.report(beat("w1"), NOW).cordoned is False


def test_cordoning_a_name_nobody_has_is_refused_so_a_typo_never_reads_as_done() -> None:
    assert Roster().cordon("nobody") is False


def test_a_draining_worker_is_up_but_accepts_nothing() -> None:
    roster = Roster()
    roster.report(Heartbeat(worker="w1", active=2, max_jobs=4, load=0.5, draining=True), NOW)
    totals = roster.totals(NOW)
    assert (totals.workers, totals.accepting) == (1, 0)


def test_a_seat_silent_for_an_hour_is_forgotten_and_one_silent_for_a_minute_is_shown_gone() -> None:
    """A deleted machine's ghost left `fleet list` only with a gateway restart, the first day."""
    roster = Roster()
    roster.report(beat("deleted"), NOW)
    roster.report(beat("quiet"), NOW + FORGOTTEN_AFTER_S - 60)
    listed = roster.seats(NOW + FORGOTTEN_AFTER_S + 1)
    assert [seat.worker for seat in listed] == ["quiet"]
    assert listed[0].heard_lately(NOW + FORGOTTEN_AFTER_S + 1) is False
