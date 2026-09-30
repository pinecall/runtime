"""Tests for the fleet loop: what one tick decides, and the cloud script it drives."""

import io
import stat
from pathlib import Path

import pytest

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.fleet.hub import (
    Cloud,
    Cordon,
    Delete,
    Grow,
    Line,
    Machine,
    applied,
    decide,
    free_names,
    printed,
    status_line,
    worded,
)
from pinecall.wire.rest.fleet import WorkerStatus

NOW = 1_000.0
LINE = Line(target=0.6, slack=0.15, at_least=1, at_most=4, seats_per_worker=4)


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


def a_machine(name: str, *, made_ago: float = 3_600.0) -> Machine:
    """A machine the cloud lists as the fleet's."""
    return Machine(name=name, created_at=NOW - made_ago)


def test_a_fleet_under_its_minimum_grows_by_the_lowest_free_name() -> None:
    decided = decide([], [a_machine("pinecall-worker-3", made_ago=30.0)], LINE, NOW)
    assert decided == []
    assert decide([], [], LINE, NOW) == [Grow("pinecall-worker-1", "0 of at least 1 workers")]


def test_a_busy_fleet_grows_and_a_quiet_one_cordons_its_quietest_managed_worker() -> None:
    busy = [a_seat("pinecall-worker-1", active=3), a_seat("pinecall-worker-2", active=3)]
    machines = [a_machine("pinecall-worker-1"), a_machine("pinecall-worker-2")]
    assert decide(busy, machines, LINE, NOW) == [
        Grow("pinecall-worker-3", "busy 0.75 over 0.60: 2 seats missing")
    ]
    quiet = [a_seat("pinecall-worker-1", active=1), a_seat("pinecall-worker-2", active=0)]
    (letting_go,) = decide(quiet, machines, LINE, NOW)
    assert isinstance(letting_go, Cordon)
    assert letting_go.worker == "pinecall-worker-2"


def test_a_worker_stood_up_by_hand_counts_and_is_never_let_go() -> None:
    quiet = [a_seat("by-hand", active=0), a_seat("pinecall-worker-1", active=0)]
    machines = [a_machine("pinecall-worker-1")]
    (letting_go,) = decide(quiet, machines, LINE, NOW)
    assert letting_go == Cordon("pinecall-worker-1", "busy 0.00 without it, under 0.45")
    assert decide([a_seat("by-hand", active=0)], [], LINE, NOW) == []


def test_a_booting_machine_counts_as_capacity_so_the_loop_asks_once_and_waits() -> None:
    seats = [a_seat("pinecall-worker-1", active=4)]
    machines = [a_machine("pinecall-worker-1"), a_machine("pinecall-worker-2", made_ago=30.0)]
    assert decide(seats, machines, LINE, NOW) == []


def test_a_machine_that_never_dialled_in_or_fell_silent_is_deleted() -> None:
    seats = [a_seat("pinecall-worker-1"), a_seat("pinecall-worker-2", seen_ago=400.0)]
    machines = [
        a_machine("pinecall-worker-1"),
        a_machine("pinecall-worker-2"),
        a_machine("pinecall-worker-3", made_ago=700.0),
    ]
    decided = decide(seats, machines, LINE, NOW)
    assert Delete("pinecall-worker-2", "silent for 400s") in decided
    assert Delete("pinecall-worker-3", "never dialled in within 600s") in decided


def test_a_cordoned_machine_that_holds_nothing_is_deleted_without_waiting() -> None:
    seats = [a_seat("pinecall-worker-1", active=1), a_seat("pinecall-worker-2", cordoned=True)]
    machines = [a_machine("pinecall-worker-1"), a_machine("pinecall-worker-2")]
    decided = decide(seats, machines, LINE, NOW)
    assert Delete("pinecall-worker-2", "cordoned and drained") in decided


def test_the_names_fill_the_lowest_gaps() -> None:
    machines = [a_machine("pinecall-worker-1"), a_machine("pinecall-worker-3"), a_machine("other")]
    assert free_names(machines, 1) == ["pinecall-worker-2"]
    assert free_names(machines, 3) == [
        "pinecall-worker-2",
        "pinecall-worker-4",
        "pinecall-worker-5",
    ]


WIDE = Line(target=0.6, at_least=2, at_most=20, seats_per_worker=4, grow_at_most=5)


def busy_fleet(workers: int, active_each: int) -> tuple[list[WorkerStatus], list[Machine]]:
    """A fleet of machines the loop made, each holding this many calls of its four seats."""
    names = [f"pinecall-worker-{number}" for number in range(1, workers + 1)]
    return [a_seat(name, active=active_each) for name in names], [a_machine(n) for n in names]


# (workers, calls each, line, machines asked for): busy is calls / (4 seats x workers), and the
# seats missing are those that bring it back to 0.6.
@pytest.mark.parametrize(
    ("workers", "each", "line", "grown"),
    [
        # 12 of 12 held: 20 seats wanted, 8 missing, two machines.
        (3, 4, WIDE, 2),
        # Exactly at the target is not over it: 15 of 20 seats at 0.75.
        (5, 3, Line(target=0.75, at_least=1, at_most=20, seats_per_worker=4), 0),
        # 40 of 40 held: 67 seats wanted, 27 missing, seven machines, five this tick.
        (10, 4, WIDE, 5),
        # The same with the loop as it was: one a tick.
        (10, 4, Line(target=0.6, at_least=1, at_most=20, seats_per_worker=4), 1),
        # --max leaves room for two.
        (18, 4, WIDE, 2),
        # --max reached: nothing, however busy.
        (20, 4, WIDE, 0),
        # Under --min and quiet: up to the minimum.
        (0, 0, Line(at_least=4, at_most=20, seats_per_worker=4, grow_at_most=10), 4),
        # Under --min and busy: whichever asks for more.
        (1, 4, Line(at_least=2, at_most=20, seats_per_worker=4, grow_at_most=10), 1),
        (2, 4, Line(at_least=3, at_most=20, seats_per_worker=4, grow_at_most=10), 2),
    ],
)
def test_the_loop_grows_by_the_seats_missing_within_its_ceilings(
    workers: int, each: int, line: Line, grown: int
) -> None:
    seats, machines = busy_fleet(workers, each)
    grows = [item for item in decide(seats, machines, line, NOW) if isinstance(item, Grow)]
    assert len(grows) == grown
    assert len({item.name for item in grows}) == grown
    assert not {item.name for item in grows} & {machine.name for machine in machines}


def test_the_decimal_target_is_read_as_written_not_as_a_float() -> None:
    # 9 held of 12: 9 / 0.6 is 15 seats, three missing; in floats it is 15.000000000000002.
    seats, machines = busy_fleet(3, 3)
    (grow,) = decide(seats, machines, WIDE, NOW)
    assert grow == Grow("pinecall-worker-4", "busy 0.75 over 0.60: 3 seats missing")


def test_shrinking_stays_one_cordon_a_tick_however_quiet() -> None:
    seats, machines = busy_fleet(10, 0)
    decided = decide(seats, machines, WIDE, NOW)
    assert len(decided) == 1
    assert isinstance(decided[0], Cordon)


def test_the_status_line_and_the_decisions_read_as_sentences() -> None:
    seats = [a_seat("pinecall-worker-1", active=2)]
    line = status_line(seats, [a_machine("pinecall-worker-1")], LINE, NOW, [])
    assert line == (
        "fleet: 1 workers up · 1 machines · 2/4 seats held · 1 accepting · busy 0.50 "
        "(target 0.60) · hold"
    )
    assert worded(Grow("pinecall-worker-2", "why")) == "grow    pinecall-worker-2: why"
    assert worded(Delete("x", "gone")) == "delete  x: gone"
    out = io.StringIO()
    printed(out, "one line")
    assert out.getvalue() == "one line\n"


def a_script(tmp_path: Path, body: str) -> Path:
    """An executable cloud script."""
    script = tmp_path / "cloud"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_the_cloud_is_one_script_with_three_verbs(tmp_path: Path) -> None:
    log = tmp_path / "verbs"
    script = a_script(
        tmp_path,
        f'echo "$@" >> {log}\n'
        'case "$1" in list) printf "pinecall-worker-1\\t2026-09-28T10:00:00+00:00\\n";; esac',
    )
    cloud = Cloud(script)
    (machine,) = cloud.machines()
    assert machine.name == "pinecall-worker-1"
    applied([Grow("pinecall-worker-2", ""), Delete("pinecall-worker-1", "")], lambda _: None, cloud)
    assert log.read_text().splitlines() == [
        "list",
        "create pinecall-worker-2",
        "delete pinecall-worker-1",
    ]


def test_a_cordon_decision_reaches_the_gateway_and_not_the_cloud(tmp_path: Path) -> None:
    cordoned: list[str] = []
    cloud = Cloud(a_script(tmp_path, "exit 0"))
    applied([Cordon("pinecall-worker-1", "")], cordoned.append, cloud)
    assert cordoned == ["pinecall-worker-1"]


def test_a_script_that_fails_or_is_missing_is_said_in_its_own_words(tmp_path: Path) -> None:
    with pytest.raises(DeclarationRefused, match="no cloud script"):
        Cloud(tmp_path / "nowhere")
    cloud = Cloud(a_script(tmp_path, 'echo "quota exceeded" >&2; exit 1'))
    with pytest.raises(UpstreamFailed, match="quota exceeded"):
        cloud.machines()
    unreadable = Cloud(a_script(tmp_path, 'printf "x\\tnot-a-time\\n"'))
    with pytest.raises(UpstreamFailed, match="ISO 8601"):
        unreadable.machines()
