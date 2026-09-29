"""Tests for an agent's own judges: the questions its org writes for its calls."""

import pytest

from pinecall.domain.agent import AgentJudge
from pinecall.domain.errors import NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy.judges import drop_judge, judges_of, put_judge
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
