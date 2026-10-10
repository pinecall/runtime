"""Tests for the judges an org writes, the org's and one agent's, and the library's switches."""

import pytest

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.judging import JudgeSpec
from pinecall.domain.scope import THE_ORGS_OWN
from pinecall.postgres.pool import Pool
from pinecall.tenancy.judges import (
    Switched,
    drop_judge,
    for_call,
    judges_of,
    put_judge,
    switch,
    switched_at,
    switches_for,
)
from pinecall.tenancy.orgs import remove
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

SLOT = JudgeSpec(name="offers-next-slot", question="The agent offered the next free slot.")


@postgres
async def test_a_judge_written_comes_back_whole_with_who_wrote_it(pool: Pool) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    [kept] = await judges_of(pool, org.id, "recepcion")
    assert (kept.judge, kept.author) == (SLOT, "m_ana")


@postgres
async def test_a_judge_comes_back_with_its_answer_choices_trigger_and_what_it_reads(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    sorted_out = JudgeSpec(
        name="why-they-called",
        question="Why did the caller call?",
        answer="choice",
        choices=("book", "cancel", "other"),
        on="trigger",
        trigger="The caller asked for something.",
        reads_prompt=True,
        reads_facts=True,
    )
    await put_judge(pool, org.id, "recepcion", sorted_out, author="m_ana")
    [kept] = await judges_of(pool, org.id, "recepcion")
    assert (kept.judge, kept.agent) == (sorted_out, "recepcion")


@postgres
async def test_writing_the_same_name_again_replaces_the_question_and_when_it_runs(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    again = JudgeSpec(name=SLOT.name, question="Offered two slots.", on="simulations")
    await put_judge(pool, org.id, "recepcion", again, author="m_ben")
    [kept] = await judges_of(pool, org.id, "recepcion")
    assert (kept.judge, kept.author) == (again, "m_ben")


@postgres
async def test_an_agent_reads_its_own_judges_and_never_another_agents(pool: Pool) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    other = JudgeSpec(name="says-the-price", question="The agent said the price.")
    await put_judge(pool, org.id, "ventas", other, author="m_ana")
    assert [kept.judge for kept in await judges_of(pool, org.id, "recepcion")] == [SLOT]
    assert [kept.judge for kept in await judges_of(pool, org.id, "ventas")] == [other]


@postgres
async def test_the_same_agent_in_another_org_has_none_of_them(pool: Pool) -> None:
    ours, theirs = await an_org(pool), await an_org(pool, "tienda-sur")
    await put_judge(pool, ours.id, "recepcion", SLOT, author="m_ana")
    assert await judges_of(pool, theirs.id, "recepcion") == []


@postgres
async def test_judges_are_listed_by_name(pool: Pool) -> None:
    org = await an_org(pool)
    for name in ("zeta", "alfa", "media"):
        await put_judge(pool, org.id, "recepcion", JudgeSpec(name, "q"), author="m_ana")
    names = [kept.judge.name for kept in await judges_of(pool, org.id, "recepcion")]
    assert names == ["alfa", "media", "zeta"]


@postgres
async def test_one_dropped_is_gone_and_a_name_nobody_wrote_is_not_found(pool: Pool) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    await drop_judge(pool, org.id, "recepcion", SLOT.name)
    assert await judges_of(pool, org.id, "recepcion") == []
    with pytest.raises(NotFound, match="recepcion has no judge called offers-next-slot"):
        await drop_judge(pool, org.id, "recepcion", SLOT.name)


@postgres
async def test_an_org_deleted_takes_its_agents_judges_with_it(pool: Pool) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    assert await remove(pool, org.id)
    assert await judges_of(pool, org.id, "recepcion") == []


@postgres
async def test_the_orgs_judges_are_written_at_the_orgs_own_level_and_a_call_is_held_to_both(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    general = JudgeSpec(name="never-medical-advice", question="The agent gave no medical advice.")
    await put_judge(pool, org.id, THE_ORGS_OWN, general, author="m_ana")
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    assert [kept.judge for kept in await judges_of(pool, org.id, THE_ORGS_OWN)] == [general]
    assert [kept.judge for kept in await judges_of(pool, org.id, "recepcion")] == [SLOT]
    assert [kept.judge for kept in await for_call(pool, org.id, "recepcion")] == [general, SLOT]
    assert [kept.judge for kept in await for_call(pool, org.id, "ventas")] == [general]


@postgres
async def test_a_name_is_one_question_of_a_call_so_the_orgs_and_an_agents_never_share_it(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, THE_ORGS_OWN, SLOT, author="m_ana")
    with pytest.raises(Conflict, match="offers-next-slot is the org judge already"):
        await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    await drop_judge(pool, org.id, THE_ORGS_OWN, SLOT.name)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    await put_judge(pool, org.id, "ventas", SLOT, author="m_ana")
    with pytest.raises(Conflict, match="is recepcion judge already"):
        await put_judge(pool, org.id, THE_ORGS_OWN, SLOT, author="m_ana")


@postgres
async def test_whose_a_judge_is_comes_back_with_it(pool: Pool) -> None:
    org = await an_org(pool)
    general = JudgeSpec(name="polite", question="The agent was polite.")
    await put_judge(pool, org.id, THE_ORGS_OWN, general, author="m_ana")
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    owners = [(kept.judge.name, kept.agent) for kept in await for_call(pool, org.id, "recepcion")]
    assert owners == [("polite", ""), ("offers-next-slot", "recepcion")]


# ── the library's switches ──


@postgres
async def test_a_switch_nobody_wrote_is_absent_so_the_librarys_default_stands(pool: Pool) -> None:
    org = await an_org(pool)
    assert await switches_for(pool, org.id, "recepcion") == {}


@postgres
async def test_the_agents_switch_wins_over_the_orgs(pool: Pool) -> None:
    org = await an_org(pool)
    await switch(
        pool, org.id, THE_ORGS_OWN, Switched(("sentiment", "relevance"), on=True, author="m_ana")
    )
    await switch(pool, org.id, "recepcion", Switched(("sentiment",), on=False, author="m_ben"))
    assert await switches_for(pool, org.id, "recepcion") == {
        "sentiment": False,
        "relevance": True,
    }
    assert await switches_for(pool, org.id, "ventas") == {"sentiment": True, "relevance": True}


@postgres
async def test_a_level_reads_its_own_switches_alone_and_a_second_switch_replaces_the_first(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await switch(pool, org.id, THE_ORGS_OWN, Switched(("consent",), on=False, author="m_ana"))
    await switch(pool, org.id, "recepcion", Switched(("sentiment",), on=True, author="m_ana"))
    await switch(pool, org.id, "recepcion", Switched(("sentiment",), on=False, author="m_ben"))
    assert await switched_at(pool, org.id, THE_ORGS_OWN) == {"consent": False}
    assert await switched_at(pool, org.id, "recepcion") == {"sentiment": False}


@postgres
async def test_another_orgs_switches_are_never_read(pool: Pool) -> None:
    ours, theirs = await an_org(pool), await an_org(pool, "tienda-sur")
    await switch(pool, ours.id, THE_ORGS_OWN, Switched(("consent",), on=False, author="m_ana"))
    assert await switches_for(pool, theirs.id, "recepcion") == {}


@postgres
async def test_a_refusal_names_the_org_where_the_judge_would_be_the_orgs(pool: Pool) -> None:
    org = await an_org(pool)
    with pytest.raises(NotFound, match="the org has no judge called offers-next-slot"):
        await drop_judge(pool, org.id, THE_ORGS_OWN, SLOT.name)
