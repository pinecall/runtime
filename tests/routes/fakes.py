"""The media plane of every test: what the SFU would hold, kept in memory and read back."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field

from pinecall.routes.dispatching import Job
from pinecall.routes.outbound import Placing
from pinecall.routes.trunks import TRUNK_NAME
from pinecall.types.dispatch import DEFAULT_FLEET


@dataclass
class _Admitted:
    trunk_id: str
    numbers: set[str] = field(default_factory=set[str])
    allowed: tuple[str, ...] = ()
    auth: tuple[str, str] | None = None


class MemoryTrunks:
    """What the SFU would hold: each org's trunk named as the default fleet names it. `elsewhere`
    is what trunks this runtime did not make list: a number, and the name of the trunk that holds
    it."""

    def __init__(self, elsewhere: dict[str, str] | None = None) -> None:
        self.trunks: dict[str, _Admitted] = {}
        self.elsewhere = dict(elsewhere or {})

    async def admitted(
        self, org: str, number: str, allowed: Sequence[str], auth: tuple[str, str] | None
    ) -> str:
        trunk = self.trunks.setdefault(org, _Admitted(trunk_id=f"ST_{org}"))
        trunk.numbers.add(number)
        trunk.allowed = tuple(allowed)
        trunk.auth = auth
        return trunk.trunk_id

    async def released(self, org: str, number: str) -> bool:
        trunk = self.trunks.get(org)
        if trunk is None or number not in trunk.numbers:
            return False
        trunk.numbers.discard(number)
        return True

    async def held_elsewhere(self, org: str, number: str) -> str | None:
        for held, trunk in self.trunks.items():
            if held != org and number in trunk.numbers:
                return TRUNK_NAME.format(fleet=DEFAULT_FLEET, org=held)
        return self.elsewhere.get(number)


@dataclass
class _Placed:
    trunk_id: str
    placing: Placing


class MemoryOutbound:
    """What the SFU would hold."""

    def __init__(self) -> None:
        self.trunks: dict[str, _Placed] = {}

    async def standing(self, org: str) -> str | None:
        placed = self.trunks.get(org)
        return None if placed is None else placed.trunk_id

    async def provisioned(self, org: str, placing: Placing) -> str:
        placed = self.trunks.get(org)
        trunk_id = f"ST_{org}_out" if placed is None else placed.trunk_id
        self.trunks[org] = _Placed(trunk_id=trunk_id, placing=placing)
        return trunk_id


class MemoryDispatches:
    """What would have been dispatched."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def started(self, job: Job) -> None:
        self.jobs.append(job)


class MemoryRooms:
    """What the SFU would hold."""

    def __init__(self, open: Iterable[str] = (), agentless: Iterable[str] = ()) -> None:
        # The rooms an agent is in, and the ones only people are left in.
        self.open = set(open)
        self.agentless = set(agentless)
        self.taken_down: list[str] = []

    async def with_an_agent(self, names: Collection[str]) -> set[str]:
        return {name for name in names if name in self.open}

    async def closed(self, name: str) -> None:
        self.open.discard(name)
        self.agentless.discard(name)
        self.taken_down.append(name)
