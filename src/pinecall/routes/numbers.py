"""Every number an org answers at, read from the one table that holds them."""

from __future__ import annotations

from pinecall.routes.records import Routes
from pinecall.types import PRODUCTION, SANDBOX, Env

# Production first, so the number a tenant would name is the first one a plan or a fence shows.
ENVS_IN_ORDER: tuple[Env, ...] = (PRODUCTION, SANDBOX)


# A number is the org's in either world — a route moves between them and the carrier never
# notices — so the trunk that may show one, and the country fence worked out from them, read
# both. It is here rather than beside either door because two of them ask: the outbound trunk's
# `numbers`, and the guards that fence a dial by the countries the org already answers in.
async def own_numbers(table: Routes, org: str) -> tuple[str, ...]:
    """Every phone number this org answers at, both worlds, in the order the table holds them."""
    found: list[str] = []
    for env in ENVS_IN_ORDER:
        for route in await table.of_org(org, env):
            if route.channel == "phone" and route.number is not None and route.number not in found:
                found.append(route.number)
    return tuple(found)
