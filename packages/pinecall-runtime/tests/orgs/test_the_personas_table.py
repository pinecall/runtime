"""The org's personas in Postgres: written whole, renamed, dropped, and JSON back as it went in."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import uuid4

import pytest

from pinecall.db import Pool, open_pool
from pinecall.orgs.personas import NameTaken, NoSuchPersona, Personas
from tests.pools import Held, acquired
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

DANA: dict[str, Any] = {
    "about": "A homeowner moving out.",
    "goal": "get a move-out cleaning quote",
    "style": "friendly, a little rushed",
    "facts": {"their name": "Dana Ruiz"},
    "state": {},
    "author": "Ana",
}


def dana(**over: Any) -> dict[str, Any]:
    """The caller every test writes, with whatever this one changes about them."""
    return {**DANA, **over}


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    """A pool on this run's schema."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def org(pool: Pool) -> str:
    """An org of this test's own: the rows outlive the test, and 0043 references it."""
    return await an_org(pool)


@pytest.fixture
async def other(pool: Pool) -> str:
    """A second org, to prove one org's callers are not another's."""
    return await an_org(pool)


async def an_org(pool: Pool) -> str:
    """One org row nobody else holds."""
    id = f"org-{uuid4().hex[:12]}"
    await pool.execute("INSERT INTO orgs (id, slug, name) VALUES ($1, $1, $1)", id)
    return id


# A rename used to be an INSERT and then a DELETE, so anything between them left the agent with
# both names. Counting the statements is how a test says "one write" about something whose whole
# point is that nothing can happen in the middle of it.
class Counted:
    """The pool, with every statement it was asked to run counted on the way through."""

    def __init__(self, pool: Pool) -> None:
        self.pool = pool
        self.statements: list[str] = []

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None:
        return await self.pool.fetchrow(query, *args)

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]:
        return await self.pool.fetch(query, *args)

    async def execute(self, query: str, /, *args: Any) -> str:
        self.statements.append(query)
        return await self.pool.execute(query, *args)

    def acquire(self) -> AbstractAsyncContextManager[Held]:
        return acquired(self)

    async def close(self) -> None:
        await self.pool.close()


async def names_in_the_table(pool: Pool, org: str) -> list[str]:
    """What the table itself holds for this org, asked of it and not of the verb under test."""
    rows = await pool.fetch("SELECT name FROM agent_personas WHERE org = $1 ORDER BY name", org)
    return [str(row["name"]) for row in rows]


async def test_a_caller_written_comes_back_whole(pool: Pool, org: str) -> None:
    kept = Personas(pool)

    [one] = await kept.put(org, "homeowner", **DANA)

    assert (one["name"], one["goal"], one["about"]) == ("homeowner", DANA["goal"], DANA["about"])
    assert one["facts"] == {"their name": "Dana Ruiz"}
    assert one["author"] == "Ana"
    assert one["set_at"] > 0


async def test_the_state_of_a_caller_the_business_knows_survives_the_column(
    pool: Pool, org: str
) -> None:
    kept = Personas(pool)
    state = {"stage": "book", "patient": {"id": "p-1", "slots": [1, 2]}}

    [one] = await kept.put(org, "known", **dana(state=state))

    assert one["state"] == state


async def test_how_a_caller_is_played_and_when_it_accepts_survive_the_columns(
    pool: Pool, org: str
) -> None:
    kept = Personas(pool)
    played = {
        "llm": "anthropic/claude-haiku-4-5",
        "tts": "cartesia/sonic-3",
        "voice": "a-uuid",
        "accepts_when": "a price for Friday",
        "declines_when": "a call back",
    }

    [one] = await kept.put(org, "played", **dana(**played))

    assert {field: one[field] for field in played} == played


async def test_a_caller_written_before_it_could_say_so_is_played_by_the_runtime(
    pool: Pool, org: str
) -> None:
    """0047's defaults: NULL knobs are the runtime's choice, and an empty rule judges nothing."""
    await pool.execute(
        "INSERT INTO agent_personas (org, name, goal, style) VALUES ($1, 'old', 'g', 's')", org
    )

    [one] = await Personas(pool).of(org)

    assert (one["llm"], one["tts"], one["voice"]) == (None, None, None)
    assert (one["accepts_when"], one["declines_when"]) == ("", "")


async def test_writing_the_same_name_replaces_it_and_never_doubles_it(pool: Pool, org: str) -> None:
    kept = Personas(pool)
    await kept.put(org, "homeowner", **DANA)

    written = await kept.put(org, "homeowner", **dana(goal="a price today"))

    assert [one["goal"] for one in written] == ["a price today"]


async def test_a_rename_leaves_exactly_one_row_and_it_is_the_new_name(pool: Pool, org: str) -> None:
    """The old name is gone from the table itself, not merely absent from what put() answered."""
    kept = Personas(pool)
    await kept.put(org, "homeowner", **DANA)

    renamed = await kept.put(org, "dana", **dana(goal="a price today"), was="homeowner")

    assert [one["name"] for one in renamed] == ["dana"]
    assert await names_in_the_table(pool, org) == ["dana"]
    assert renamed[0]["goal"] == "a price today"


async def test_a_rename_is_one_statement_so_no_cut_leaves_both_names(pool: Pool, org: str) -> None:
    """Gone and written together: what used to be an INSERT, a gap, and then a DELETE."""
    counted = Counted(pool)
    kept = Personas(counted)
    await kept.put(org, "homeowner", **DANA)
    counted.statements.clear()

    await kept.put(org, "dana", **DANA, was="homeowner")

    assert len(counted.statements) == 1
    assert await names_in_the_table(pool, org) == ["dana"]


async def test_a_rename_writes_nothing_when_nobody_wrote_the_name_it_renames(
    pool: Pool, org: str
) -> None:
    kept = Personas(pool)
    await kept.put(org, "homeowner", **DANA)

    with pytest.raises(NoSuchPersona):
        await kept.put(org, "dana", **DANA, was="nobody")

    assert await names_in_the_table(pool, org) == ["homeowner"]


async def test_a_rename_onto_a_name_somebody_holds_is_refused_and_moves_nothing(
    pool: Pool, org: str
) -> None:
    kept = Personas(pool)
    await kept.put(org, "homeowner", **DANA)
    await kept.put(org, "dana", **DANA)

    with pytest.raises(NameTaken):
        await kept.put(org, "dana", **DANA, was="homeowner")

    assert await names_in_the_table(pool, org) == ["dana", "homeowner"]


async def test_dropping_one_nobody_wrote_says_so(pool: Pool, org: str) -> None:
    kept = Personas(pool)

    with pytest.raises(NoSuchPersona):
        await kept.drop(org, "nobody")


async def test_a_caller_written_once_is_read_by_every_agent_of_the_org(
    pool: Pool, org: str
) -> None:
    """The row names no agent, so there is one list and every agent of the org calls with it."""
    kept = Personas(pool)
    await kept.put(org, "homeowner", **DANA)

    assert [one["name"] for one in await kept.of(org)] == ["homeowner"]
    assert await names_in_the_table(pool, org) == ["homeowner"]


async def test_one_orgs_callers_are_not_anothers(pool: Pool, org: str, other: str) -> None:
    kept = Personas(pool)
    await kept.put(org, "homeowner", **DANA)

    assert await kept.of(other) == []
    assert [one["name"] for one in await kept.of(org)] == ["homeowner"]
