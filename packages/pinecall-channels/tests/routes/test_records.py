"""The table a process with no database keeps, and the one rule both tables share."""

from typing import cast

import pytest

from pinecall.db import Pool
from pinecall.routes.records import door_of, routes_for
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.routes.records_postgres import PostgresRoutes
from pinecall.types import PRODUCTION, DeclarationRefused, Route

pytestmark = pytest.mark.unit

ORG = "clinica"
NUMBER = "+59829000000"
TYPED = Route(org=ORG, agent="tienda-sur", channel="phone", number=NUMBER)


async def test_a_number_added_twice_moves_and_never_doubles() -> None:
    """The same rule the primary key enforces, in the table a dev clone runs on."""
    table = MemoryRoutes([Route(org=ORG, agent="clinica-norte", channel="phone", number=NUMBER)])
    await table.put(TYPED)
    assert await table.of_org(ORG, PRODUCTION) == (TYPED,)


async def test_removing_says_whether_there_was_a_row_to_remove() -> None:
    """A typo in `routes rm` is told apart from a number that was really there."""
    table = MemoryRoutes([TYPED])
    assert await table.remove(ORG, NUMBER) is True
    assert await table.remove(ORG, NUMBER) is False


async def test_the_numbers_the_box_bought_are_counted_apart_from_the_imported() -> None:
    """What the `numbers` quota is measured on: the managed rows, in both worlds, and no other."""
    bought = Route(
        org=ORG, agent="tienda-sur", channel="phone", number="+14175550100", managed=True
    )
    table = MemoryRoutes([TYPED, bought])
    assert await table.managed_by(ORG) == 1
    assert await table.managed_by("somebody-else") == 0


async def test_another_fleets_routes_are_not_this_fleets() -> None:
    """The org is the first half of the key, in memory exactly as in the database."""
    table = MemoryRoutes([TYPED])
    assert await table.of_org("somebody-else", PRODUCTION) == ()


def test_a_route_with_no_number_is_refused_before_it_reaches_the_key() -> None:
    """The widget is not a door an operator types: there is nothing to dial."""
    with pytest.raises(DeclarationRefused, match="answers at a number"):
        door_of(Route(org=ORG, agent="clinica-norte", channel="web"))


def test_the_factory_follows_the_pool_the_process_opened() -> None:
    """A clone on a dev key routes in memory and forgets; a box with a pool reads the table."""
    assert isinstance(routes_for(None), MemoryRoutes)
    assert isinstance(routes_for(cast("Pool", object())), PostgresRoutes)
