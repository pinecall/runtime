"""One tick: the hub and the cloud read, every decision acted on, and one line a person reads."""

from __future__ import annotations

import io

import pytest

from pinecall.fleet import Cordon, Delete, Grow, Line, Machine, Seat
from pinecall.fleet.loop import tick

pytestmark = pytest.mark.unit

NOW = 20_000.0
LINE = Line(target=0.6, at_least=1, at_most=3, seats_per_worker=4)


class FakeHub:
    def __init__(self, seats: list[Seat]) -> None:
        self._seats = seats
        self.cordoned: list[str] = []

    async def seats(self) -> tuple[Seat, ...]:
        return tuple(self._seats)

    async def cordon(self, worker: str) -> None:
        self.cordoned.append(worker)


class FakeCloud:
    def __init__(self, machines: list[Machine]) -> None:
        self._machines = machines
        self.created: list[str] = []
        self.deleted: list[str] = []

    def machines(self) -> tuple[Machine, ...]:
        return tuple(self._machines)

    def create(self, name: str) -> None:
        self.created.append(name)

    def delete(self, name: str) -> None:
        self.deleted.append(name)


def seat(name: str, active: int, *, cordoned: bool = False) -> Seat:
    return Seat(
        worker=name,
        active=active,
        max_jobs=4,
        load=active / 4,
        draining=False,
        cordoned=cordoned,
        seen_at=NOW,
    )


async def test_a_busy_fleet_grows_and_the_line_says_so() -> None:
    hub = FakeHub([seat("pinecall-worker-1", 3)])
    cloud = FakeCloud([Machine("pinecall-worker-1", NOW - 3_600)])
    out = io.StringIO()
    decided = await tick(hub, cloud, LINE, NOW, out)
    assert decided == (Grow("pinecall-worker-2", "busy 0.75 over 0.60"),)
    assert cloud.created == ["pinecall-worker-2"]
    assert (
        "1 workers up · 1 machines · 3/4 seats held · busy 0.75 (target 0.60) · 1 to do"
        in out.getvalue()
    )
    assert "grow    pinecall-worker-2: busy 0.75 over 0.60" in out.getvalue()


async def test_a_drained_cordon_is_deleted_and_an_idle_worker_cordoned() -> None:
    hub = FakeHub(
        [
            seat("pinecall-worker-1", 1),
            seat("pinecall-worker-2", 0, cordoned=True),
            seat("pinecall-worker-3", 0),
        ]
    )
    cloud = FakeCloud([Machine(f"pinecall-worker-{n}", NOW - 3_600) for n in (1, 2, 3)])
    decided = await tick(hub, cloud, LINE, NOW, io.StringIO())
    assert Delete("pinecall-worker-2", "cordoned and drained") in decided
    assert any(isinstance(one, Cordon) and one.worker == "pinecall-worker-3" for one in decided)
    assert cloud.deleted == ["pinecall-worker-2"]
    assert hub.cordoned == ["pinecall-worker-3"]


async def test_a_dry_run_decides_the_same_and_touches_nothing() -> None:
    hub = FakeHub([seat("pinecall-worker-1", 3)])
    cloud = FakeCloud([Machine("pinecall-worker-1", NOW - 3_600)])
    out = io.StringIO()
    decided = await tick(hub, cloud, LINE, NOW, out, dry_run=True)
    assert len(decided) == 1
    assert (cloud.created, cloud.deleted, hub.cordoned) == ([], [], [])
    assert "(dry run)" in out.getvalue()
