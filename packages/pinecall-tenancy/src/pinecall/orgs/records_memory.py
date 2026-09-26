"""Orgs in this process's memory: the port's spec by example, and a gateway with no database."""

from __future__ import annotations

from collections.abc import Sequence

from pinecall.orgs.records import new_org_id
from pinecall.types import DEFAULT_ORG, Org, Quotas


class MemoryOrgs:
    """The tenants of a process with no database: a dev clone has the default org and forgets."""

    # `rows` are tenants that arrive with ids of their own, as the ones 0006 migrated do — what
    # a test hands in beside the key records it minted for them.
    def __init__(self, rows: Sequence[Org] = ()) -> None:
        self._rows: dict[str, Org] = {DEFAULT_ORG: Org(DEFAULT_ORG, DEFAULT_ORG, DEFAULT_ORG)}
        self._rows.update({org.id: org for org in rows})
        self._quotas: dict[str, Quotas] = {}
        self._not_judged: set[str] = set()

    async def create(self, slug: str, name: str) -> Org | None:
        """One org per slug, as the table's UNIQUE would insist."""
        if any(org.slug == slug for org in self._rows.values()):
            return None
        org = Org(id=new_org_id(), slug=slug, name=name)
        self._rows[org.id] = org
        return org

    async def mirrored(self, org: Org) -> Org | None:
        """Kept under production's id, unless another org holds the slug."""
        if any(held.slug == org.slug and held.id != org.id for held in self._rows.values()):
            return None
        self._rows[org.id] = org
        return org

    async def listed(self) -> tuple[Org, ...]:
        """In the order they were created, which for a dict is the order they were inserted."""
        return tuple(self._rows.values())

    async def find(self, named: str) -> Org | None:
        """By id first, then by slug: the two never collide, an id carries its prefix."""
        found = self._rows.get(named)
        if found is not None:
            return found
        return next((org for org in self._rows.values() if org.slug == named), None)

    async def remove(self, id: str) -> bool:
        """Whether there was a row to forget. Its quotas go with it."""
        self._quotas.pop(id, None)
        return self._rows.pop(id, None) is not None

    async def quotas_of(self, id: str) -> Quotas:
        """Unlimited until somebody said otherwise."""
        return self._quotas.get(id, Quotas())

    async def set_quotas(self, id: str, quotas: Quotas) -> None:
        """Replaced whole, as the row is."""
        self._quotas[id] = quotas

    async def judges(self, id: str) -> bool:
        """On unless it was turned off."""
        return id not in self._not_judged

    async def set_judging(self, id: str, on: bool) -> None:
        """Remembered as the orgs that said no."""
        if on:
            self._not_judged.discard(id)
        else:
            self._not_judged.add(id)
