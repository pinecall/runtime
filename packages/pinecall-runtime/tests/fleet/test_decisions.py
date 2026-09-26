"""What the loop decides from the roster and the cloud: grow over the target, shrink under it."""

from __future__ import annotations

import pytest

from pinecall.fleet import Cordon, Delete, Grow, Line, Machine, Seat, decide, next_name

pytestmark = pytest.mark.unit

NOW = 10_000.0
LINE = Line(target=0.6, slack=0.15, at_least=1, at_most=4, seats_per_worker=4)


def seat(
    name: str,
    active: int = 0,
    max_jobs: int | None = 4,
    *,
    cordoned: bool = False,
    seen: float = NOW,
) -> Seat:
    load = active / max_jobs if max_jobs else 0.0
    return Seat(
        worker=name,
        active=active,
        max_jobs=max_jobs,
        load=load,
        draining=False,
        cordoned=cordoned,
        seen_at=seen,
    )


def machine(name: str, age_s: float = 3_600.0) -> Machine:
    return Machine(name=name, created_at=NOW - age_s)


def test_nothing_to_do_when_the_fleet_sits_at_the_target() -> None:
    """4 of 8 is 0.5: under 0.6, and over 0.45 without a worker — so it holds."""
    seats = [seat("pinecall-worker-1", 2), seat("pinecall-worker-2", 2)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2")]
    assert decide(seats, machines, LINE, NOW) == ()


def test_over_the_target_asks_for_one_machine_under_the_next_free_name() -> None:
    seats = [seat("pinecall-worker-1", 3), seat("pinecall-worker-3", 3)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-3")]
    (grow,) = decide(seats, machines, LINE, NOW)
    assert isinstance(grow, Grow)
    assert grow.name == "pinecall-worker-2"
    assert "0.75" in grow.why


def test_a_machine_still_booting_counts_as_capacity_so_the_loop_asks_once() -> None:
    """6 of 4 measured is over; 6 of 8 with the boot counted is not. One machine, not one a tick."""
    seats = [seat("pinecall-worker-1", 3)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2", age_s=60)]
    assert decide(seats, machines, LINE, NOW) == ()


def test_a_machine_that_never_dialled_in_within_the_grace_is_deleted() -> None:
    machines = [
        machine("pinecall-worker-1"),
        machine("pinecall-worker-2", age_s=LINE.boot_grace_s + 1),
    ]
    decided = decide([seat("pinecall-worker-1", 1)], machines, LINE, NOW)
    assert Delete("pinecall-worker-2", "never dialled in within 600s") in decided


def test_a_worker_gone_silent_for_five_minutes_is_deleted_and_a_blip_is_not() -> None:
    blip = seat("pinecall-worker-2", 1, seen=NOW - 60)
    gone = seat("pinecall-worker-3", 1, seen=NOW - LINE.gone_after_s - 1)
    machines = [
        machine(name) for name in ("pinecall-worker-1", "pinecall-worker-2", "pinecall-worker-3")
    ]
    decided = decide([seat("pinecall-worker-1", 1), blip, gone], machines, LINE, NOW)
    deleted = [one.name for one in decided if isinstance(one, Delete)]
    assert deleted == ["pinecall-worker-3"]


def test_well_under_the_target_cordons_the_quietest_managed_worker() -> None:
    """1 of 8 is 0.125; without the idle one it is 1 of 4, 0.25, under 0.45: let it go."""
    seats = [seat("pinecall-worker-1", 1), seat("pinecall-worker-2", 0)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2")]
    (cordon,) = decide(seats, machines, LINE, NOW)
    assert cordon == Cordon("pinecall-worker-2", "busy 0.25 without it, under 0.45")


def test_the_slack_keeps_a_shrink_from_landing_at_the_target() -> None:
    """4 of 8 is 0.5, under the target; without one it is 4 of 4, and that is a grow next tick."""
    seats = [seat("pinecall-worker-1", 2), seat("pinecall-worker-2", 2)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2")]
    assert decide(seats, machines, LINE, NOW) == ()


def test_a_cordoned_worker_holding_nothing_is_deleted() -> None:
    seats = [seat("pinecall-worker-1", 1), seat("pinecall-worker-2", 0, cordoned=True)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2")]
    assert Delete("pinecall-worker-2", "cordoned and drained") in decide(seats, machines, LINE, NOW)


def test_a_cordoned_worker_that_fell_silent_has_left_and_its_machine_goes_now() -> None:
    """It exited 3 five seconds after the cordon; nobody waits the five minutes a blip gets."""
    gone = seat("pinecall-worker-2", 0, cordoned=True, seen=NOW - 60)
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2")]
    decided = decide([seat("pinecall-worker-1", 1), gone], machines, LINE, NOW)
    assert decided == (Delete("pinecall-worker-2", "cordoned and gone"),)


def test_a_cordoned_worker_still_on_a_call_is_left_alone() -> None:
    seats = [seat("pinecall-worker-1", 1), seat("pinecall-worker-2", 1, cordoned=True)]
    machines = [machine("pinecall-worker-1"), machine("pinecall-worker-2")]
    assert not any(isinstance(one, Delete) for one in decide(seats, machines, LINE, NOW))


def test_the_minimum_is_kept_however_idle_the_fleet_is() -> None:
    seats = [seat("pinecall-worker-1", 0)]
    assert decide(seats, [machine("pinecall-worker-1")], LINE, NOW) == ()


def test_under_the_minimum_grows_whatever_the_load() -> None:
    (grow,) = decide([], [], Line(at_least=2, seats_per_worker=4), NOW)
    assert isinstance(grow, Grow) and grow.name == "pinecall-worker-1"


def test_the_maximum_is_never_crossed() -> None:
    seats = [seat(f"pinecall-worker-{n}", 4) for n in range(1, 5)]
    machines = [machine(f"pinecall-worker-{n}") for n in range(1, 5)]
    assert not any(isinstance(one, Grow) for one in decide(seats, machines, LINE, NOW))


def test_only_a_machine_the_cloud_lists_is_ever_cordoned() -> None:
    """A worker somebody stood up by hand counts in the numbers and is never let go by the loop."""
    seats = [seat("by-hand", 0), seat("pinecall-worker-1", 0)]
    (cordon,) = decide(
        seats, [machine("pinecall-worker-1")], Line(at_least=1, seats_per_worker=4), NOW
    )
    assert isinstance(cordon, Cordon) and cordon.worker == "pinecall-worker-1"


def test_a_cpu_gated_worker_is_outside_the_arithmetic() -> None:
    """The hub's own worker has no seats to count; the loop sizes the measured ones."""
    seats = [seat("hub", 5, max_jobs=None), seat("pinecall-worker-1", 1)]
    assert decide(seats, [machine("pinecall-worker-1")], LINE, NOW) == ()


def test_the_next_name_is_the_smallest_number_nobody_holds() -> None:
    assert next_name([]) == "pinecall-worker-1"
    assert (
        next_name([machine("pinecall-worker-1"), machine("pinecall-worker-3")])
        == "pinecall-worker-2"
    )
    assert next_name([machine("something-else")]) == "pinecall-worker-1"
