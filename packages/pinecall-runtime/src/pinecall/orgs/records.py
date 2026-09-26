"""The orgs table and the quotas beside it: who the tenants are, and what each may consume."""

from __future__ import annotations

import secrets
from typing import Protocol

from pinecall.db import Pool
from pinecall.errors import PinecallError
from pinecall.types import Org, Quotas

# An id is minted, never typed: a slug may be renamed one day and every row that names the org
# must not notice. The default org and the tenants 0006 migrated are the exception — their id is
# the word the old `fleet` column held, so a key issued then still names the same row.
# A slug is an org's public name, and a box answers to each one once.
SLUG_TAKEN = "{slug} is taken: pick another name for the org"

ORG_ID_PREFIX = "org_"
ORG_ID_BYTES = 6


class SlugTaken(PinecallError):
    """The slug a new org asked for already answers for another org on this box."""


class Orgs(Protocol):
    """Where the operator's doors create, find and remove tenants, and set what each may use."""

    async def create(self, slug: str, name: str) -> Org | None:
        """A new org, or None when the slug is already somebody's. The id is minted here."""
        ...

    # A sandbox instance's orgs are production's, mirrored when a person signs in there
    # (api/accounts/identity.py): the SAME id and slug, so `pinecall link`, a key's org and every
    # door keep their words on both instances. Written over on every sign-in, so a rename at
    # production is a rename here at the next one. A slug an org of this instance's own holds under
    # another id is not taken from it.
    async def mirrored(self, org: Org) -> Org | None:
        """The org as production says it, inserted or updated by its id. None when the slug is
        another org's here."""
        ...

    async def listed(self) -> tuple[Org, ...]:
        """Every org, oldest first."""
        ...

    async def find(self, named: str) -> Org | None:
        """The org this id or slug names, or None. A door takes either; a row keeps the id."""
        ...

    async def remove(self, id: str) -> bool:
        """Forget the org and its quotas. False when no row answered to it."""
        ...

    async def quotas_of(self, id: str) -> Quotas:
        """What the org may consume. An org nobody limited has no limits."""
        ...

    async def set_quotas(self, id: str, quotas: Quotas) -> None:
        """Replace the org's limits, whole: a limit left out is no limit."""
        ...

    # The one setting a tenant turns about its own org rather than about an agent: whether its
    # calls are judged at hang-up. On unless somebody turned it off — an org nobody asked about is
    # judged, as every org was before the setting existed.
    async def judges(self, id: str) -> bool:
        """Whether this org's calls are judged at hang-up."""
        ...

    async def set_judging(self, id: str, on: bool) -> None:
        """Judge this org's calls at hang-up, or stop."""
        ...


def new_org_id() -> str:
    """A name for the row. It is not a secret: it names the tenant in every table that has one."""
    return f"{ORG_ID_PREFIX}{secrets.token_hex(ORG_ID_BYTES)}"


def orgs_for(pool: Pool | None) -> Orgs:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.records_memory import MemoryOrgs
    from pinecall.orgs.records_postgres import PostgresOrgs

    return MemoryOrgs() if pool is None else PostgresOrgs(pool)
