"""0045 against a box whose agents each wrote a caller: the key rebuilt, and nothing lost."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.migrations import Before, a_box_before
from tests.support.postgres import Dev

pytestmark = pytest.mark.postgres

THE_MIGRATION = "0045_personas_of_the_org.sql"

ORG = "org_a_box_in_use"
ANOTHER_ORG = "org_the_neighbour"

SALES = "bidfire-sales"
DISPATCH = "bidfire-dispatch"


async def a_persona(before: Before, org: str, agent: str, name: str, *, month: int) -> None:
    """One row as 0042 left it: filed under an agent, and written at a moment of its own."""
    await before.connection.execute(
        """insert into agent_personas (org, agent, name, about, goal, style, author, set_at)
           values ($1, $2, $3, '', 'a quote', 'hurried', 'Ana', $4)""",
        org,
        agent,
        name,
        datetime(2026, month, 1, 10, 0, tzinfo=UTC),
    )


@pytest.fixture
async def before(postgres: Dev) -> AsyncIterator[Before]:
    """The schema as it stood at 0044, with two orgs and the collision only a merge can make."""
    async for box in a_box_before(postgres, THE_MIGRATION):
        for org in (ORG, ANOTHER_ORG):
            await box.connection.execute(
                "insert into orgs (id, slug, name) values ($1, $1, $1)", org
            )
        # The collision: two agents of ONE org holding one name, the newer one on dispatch.
        await a_persona(box, ORG, SALES, "price-shopper", month=1)
        await a_persona(box, ORG, DISPATCH, "price-shopper", month=2)
        # And the ordinary rows: one name one agent holds, and another org's same name.
        await a_persona(box, ORG, SALES, "curious-electrician", month=1)
        await a_persona(box, ANOTHER_ORG, "clinica", "price-shopper", month=1)
        yield box


async def names_of(before: Before, org: str) -> list[str]:
    """What the table holds for that org, by name."""
    rows: list[Any] = await before.rows(
        f"select name from agent_personas where org = '{org}' order by name"
    )
    return [str(row["name"]) for row in rows]


async def test_it_applies_at_all_against_a_populated_table(before: Before) -> None:
    """A primary key rebuilt under rows: the way a migration passes on an empty database and
    takes a box down on a full one."""
    assert THE_MIGRATION in await before.take_it()


async def test_the_most_recently_written_keeps_the_name_and_the_other_keeps_its_row(
    before: Before,
) -> None:
    """A collision loses nothing: the loser is renamed to `<name>-<agent>`, not deleted."""
    await before.take_it()

    assert await names_of(before, ORG) == [
        "curious-electrician",
        "price-shopper",
        "price-shopper-bidfire-sales",
    ]
    kept = await before.rows(
        f"select agent, goal from agent_personas where org = '{ORG}' and name = 'price-shopper'"
    )
    assert [row["agent"] for row in kept] == [DISPATCH]


async def test_one_orgs_collision_leaves_another_orgs_same_name_alone(before: Before) -> None:
    """The key is (org, name): two orgs holding one name were never a collision at all."""
    await before.take_it()

    assert await names_of(before, ANOTHER_ORG) == ["price-shopper"]


async def test_a_caller_is_written_after_it_without_naming_an_agent(before: Before) -> None:
    """The column keeps its rows and loses its NOT NULL: the doors name no agent any more."""
    await before.take_it()

    await before.connection.execute(
        """insert into agent_personas (org, name, goal, style, author)
           values ($1, 'homeowner', 'a quote', 'friendly', 'Ana')""",
        ORG,
    )

    assert "homeowner" in await names_of(before, ORG)
