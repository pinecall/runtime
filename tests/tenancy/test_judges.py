"""Tests for the judges an org writes: the org's for every agent, and one agent's own."""

import pytest

from pinecall.domain.agent import AgentJudge
from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound
from pinecall.domain.scope import THE_ORGS_OWN
from pinecall.postgres.pool import Pool
from pinecall.tenancy.judges import drop_judge, for_call, judges_of, put_judge
from pinecall.tenancy.orgs import remove
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

SLOT = AgentJudge(name="offers-next-slot", question="The agent offered the next free slot.")


@postgres
async def test_a_judge_written_comes_back_whole_with_who_wrote_it(pool: Pool) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    [kept] = await judges_of(pool, org.id, "recepcion")
    assert (kept.judge, kept.author) == (SLOT, "m_ana")


@postgres
async def test_writing_the_same_name_again_replaces_the_question_and_when_it_runs(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    again = AgentJudge(name=SLOT.name, question="Offered two slots.", runs_on="simulations")
    await put_judge(pool, org.id, "recepcion", again, author="m_ben")
    [kept] = await judges_of(pool, org.id, "recepcion")
    assert (kept.judge, kept.author) == (again, "m_ben")


@postgres
async def test_an_agent_reads_its_own_judges_and_never_another_agents(pool: Pool) -> None:
    org = await an_org(pool)
    await put_judge(pool, org.id, "recepcion", SLOT, author="m_ana")
    other = AgentJudge(name="says-the-price", question="The agent said the price.")
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
        await put_judge(pool, org.id, "recepcion", AgentJudge(name, "q"), author="m_ana")
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
    general = AgentJudge(name="never-medical-advice", question="The agent gave no medical advice.")
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
async def test_a_judge_of_the_panel_or_one_with_no_question_is_refused(pool: Pool) -> None:
    org = await an_org(pool)
    with pytest.raises(DeclarationRefused, match="consent is a judge of the panel"):
        await put_judge(pool, org.id, "recepcion", AgentJudge("consent", "q"), author="m_ana")
    with pytest.raises(DeclarationRefused, match="write one"):
        await put_judge(pool, org.id, "recepcion", AgentJudge("blank", "  "), author="m_ana")
    assert await judges_of(pool, org.id, "recepcion") == []


@postgres
async def test_a_refusal_names_the_org_where_the_judge_would_be_the_orgs(pool: Pool) -> None:
    org = await an_org(pool)
    with pytest.raises(NotFound, match="the org has no judge called offers-next-slot"):
        await drop_judge(pool, org.id, THE_ORGS_OWN, SLOT.name)
