"""The routes table on a real Postgres: the migration applies, and a number moves one row."""

from typing import Any
from uuid import uuid4

import pytest

from pinecall.routes.table import PostgresRoutes
from pinecall.types import PRODUCTION, Channel, Route

pytestmark = pytest.mark.postgres

NUMBER = "+59829000000"


@pytest.fixture
def org() -> str:
    """A org nobody else in this schema uses: the table outlives a test, the rows are shared."""
    return f"org-{uuid4().hex[:12]}"


@pytest.fixture
def table(raw_connection: Any) -> PostgresRoutes:
    """The table on this run's own schema, through the connection the log suite opened."""
    return PostgresRoutes(raw_connection)


def a_route(org: str, agent: str, number: str = NUMBER, channel: Channel = "phone") -> Route:
    """One typed row, as the operator's door builds it."""
    return Route(org=org, agent=agent, channel=channel, number=number)


async def test_a_number_added_twice_moves_and_never_doubles(
    table: PostgresRoutes, org: str
) -> None:
    """The door is the primary key, so `routes add` on a number that exists is a move."""
    await table.put(a_route(org, "clinica-norte"))
    await table.put(a_route(org, "tienda-sur"))
    stored = await table.of_org(org, PRODUCTION)
    assert [(route.number, route.agent) for route in stored] == [(NUMBER, "tienda-sur")]


async def test_a_row_comes_back_as_the_domains_own_route(table: PostgresRoutes, org: str) -> None:
    """The columns are the Route's fields, name for name: nothing is converted on the way back."""
    written = a_route(org, "clinica-norte", channel="whatsapp")
    await table.put(written)
    assert await table.of_org(org, PRODUCTION) == (written,)


async def test_removing_says_whether_there_was_a_row_to_remove(
    table: PostgresRoutes, org: str
) -> None:
    """A typo in `routes rm` is told apart from a number that was really there."""
    await table.put(a_route(org, "clinica-norte"))
    assert await table.remove(org, NUMBER) is True
    assert await table.remove(org, NUMBER) is False


async def test_another_fleets_routes_are_not_this_fleets(table: PostgresRoutes, org: str) -> None:
    """The org is the first half of the key: two boxes on one database never see each other."""
    await table.put(a_route(org, "clinica-norte"))
    assert await table.of_org(f"{org}-somebody-else", PRODUCTION) == ()
