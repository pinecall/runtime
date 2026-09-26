"""Dialling in this process's memory: each org's policy and the dials it allowed."""

from __future__ import annotations

import time
from collections.abc import Callable

from pinecall.orgs.dial_policies import Dial
from pinecall.types import DialPolicy


class MemoryDialling:
    """The two tables of a process with no Postgres: a dev clone's, forgotten on exit."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._policies: dict[str, DialPolicy] = {}
        self._dials: list[tuple[str, float]] = []
        self.written: list[Dial] = []

    async def of(self, org: str) -> DialPolicy:
        return self._policies.get(org, DialPolicy())

    async def put(self, org: str, policy: DialPolicy) -> None:
        self._policies[org] = policy

    async def asked(self, dial: Dial) -> None:
        self.written.append(dial)
        self._dials.append((dial.org, self._clock()))

    async def since(self, org: str, seconds: float) -> int:
        edge = self._clock() - seconds
        return sum(1 for whose, at in self._dials if whose == org and at > edge)
