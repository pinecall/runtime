"""Tests for an agent's personas: the callers its evals play."""

import asyncio

import pytest

from pinecall.domain.errors import Conflict, NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy.personas import (
    Persona,
    PersonaEdit,
    drop_persona,
    persona,
    personas_of,
    put_persona,
)
from tests.conftest import postgres
from tests.tenancy.conftest import MARTA, an_org

DESK = "front-desk"


@postgres
async def test_a_caller_written_comes_back_whole_with_its_state_and_how_it_is_played(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    kept = await persona(pool, org.id, DESK, "marta")
    assert kept is not None
    assert (kept.persona, kept.author) == (MARTA, "m_ana")


@postgres
async def test_a_caller_written_before_it_could_say_so_is_played_by_the_runtime(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_persona(
        pool, org.id, DESK, Persona(name="juan", goal="g", style="s"), PersonaEdit("m_ana")
    )
    kept = await persona(pool, org.id, DESK, "juan")
    assert kept is not None
    assert (kept.persona.llm, kept.persona.tts, kept.persona.voice) == (None, None, None)


@postgres
async def test_a_rename_leaves_exactly_one_row_and_it_is_the_new_name(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    renamed = Persona(name="marta-2", goal=MARTA.goal, style=MARTA.style)
    await put_persona(pool, org.id, DESK, renamed, PersonaEdit("m_ana", was="marta"))
    assert [kept.persona.name for kept in await personas_of(pool, org.id, DESK)] == ["marta-2"]


@postgres
async def test_a_rename_is_one_statement_so_no_cut_leaves_both_names(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    renames = [
        put_persona(
            pool,
            org.id,
            DESK,
            Persona(name=f"m{n}", goal="g", style="s"),
            PersonaEdit("a", "marta"),
        )
        for n in range(3)
    ]
    await asyncio.gather(*renames, return_exceptions=True)
    names = [kept.persona.name for kept in await personas_of(pool, org.id, DESK)]
    assert "marta" not in names


@postgres
async def test_a_rename_of_a_name_nobody_wrote_writes_nothing(pool: Pool) -> None:
    org = await an_org(pool)
    with pytest.raises(NotFound, match="no persona called ghost"):
        await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana", was="ghost"))
    assert await personas_of(pool, org.id, DESK) == []


@postgres
async def test_a_rename_onto_a_name_somebody_holds_is_refused_and_moves_nothing(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    await put_persona(
        pool, org.id, DESK, Persona(name="juan", goal="g", style="s"), PersonaEdit("m_ana")
    )
    with pytest.raises(Conflict, match="a persona called juan already"):
        await put_persona(
            pool, org.id, DESK, Persona(name="juan", goal="g", style="s"), PersonaEdit("a", "marta")
        )
    assert [kept.persona.name for kept in await personas_of(pool, org.id, DESK)] == [
        "juan",
        "marta",
    ]


@postgres
async def test_dropping_one_nobody_wrote_says_so(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    await drop_persona(pool, org.id, DESK, "marta")
    with pytest.raises(NotFound, match="no persona called marta"):
        await drop_persona(pool, org.id, DESK, "marta")


@postgres
async def test_one_orgs_callers_are_not_anothers(pool: Pool) -> None:
    org, other = await an_org(pool), await an_org(pool, "northwind")
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    assert await personas_of(pool, other.id, DESK) == []


@postgres
async def test_one_agents_callers_are_not_anothers_even_under_the_same_name(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    theirs = Persona(name="marta", goal="ask for an invoice", style="calm")
    await put_persona(pool, org.id, "back-office", theirs, PersonaEdit("m_ana"))
    mine = await persona(pool, org.id, DESK, "marta")
    other = await persona(pool, org.id, "back-office", "marta")
    assert mine is not None
    assert other is not None
    assert (mine.persona.goal, other.persona.goal) == (MARTA.goal, "ask for an invoice")
    assert await personas_of(pool, org.id, "nobody-yet") == []


@postgres
async def test_a_caller_nobody_wrote_for_this_agent_is_not_found_for_it(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    assert await persona(pool, org.id, "back-office", "marta") is None
    with pytest.raises(NotFound, match="no persona called marta for back-office"):
        await drop_persona(pool, org.id, "back-office", "marta")


@postgres
async def test_a_rename_moves_only_this_agents_caller(pool: Pool) -> None:
    org = await an_org(pool)
    await put_persona(pool, org.id, DESK, MARTA, PersonaEdit("m_ana"))
    await put_persona(pool, org.id, "back-office", MARTA, PersonaEdit("m_ana"))
    renamed = Persona(name="marta-2", goal=MARTA.goal, style=MARTA.style)
    await put_persona(pool, org.id, DESK, renamed, PersonaEdit("m_ana", was="marta"))
    back = await personas_of(pool, org.id, "back-office")
    assert [kept.persona.name for kept in back] == ["marta"]
