"""Tests for what an org may dial: the destination's shape, strangers, the pace, the ledger."""

import asyncio

import pytest

from pinecall.domain.errors import DeclarationRefused, NotAllowed, QuotaExhausted
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy import dial_policy, orgs
from pinecall.tenancy.dial_policy import Dial
from tests.conftest import postgres

HER_PHONE = "+59899000001"
SHOWN = "+13617334133"


@pytest.fixture
async def org(pool: Pool) -> str:
    """An org nobody ever called, with the default guards."""
    return (await orgs.create(pool, "clinica", "Clinica")).id


def dial_of(org: str, to: str = HER_PHONE, env: Env = "production", call: str = "call_1") -> Dial:
    """A dial of the org's agent to that number."""
    return Dial(Scope(org, env), "recepcion", to, SHOWN, "m_ana", call)


async def ledger(pool: Pool) -> list[tuple[str, str | None, str | None]]:
    """Every dial written down: who asked, what refused it, the call it became."""
    async with pool.connection() as connection:
        rows = await (
            await connection.execute("select asked_by, refused, call from dials order by id")
        ).fetchall()
    return [(row["asked_by"], row["refused"], row["call"]) for row in rows]


# ── what a dial may reach ──


# ── the guards ──


def test_the_defaults_are_a_call_back_box_and_a_guard_is_never_negative() -> None:
    guards = dial_policy.Guards()
    assert (guards.dial_anywhere, guards.per_minute, guards.per_day, guards.max_duration_s) == (
        False,
        6,
        200,
        600,
    )
    with pytest.raises(ValueError, match="per_minute"):
        dial_policy.Guards(per_minute=-1)


@postgres
async def test_a_number_that_never_called_this_world_is_a_stranger_until_an_operator_lifts_it(
    pool: Pool, org: str
) -> None:
    with pytest.raises(NotAllowed, match="stranger"):
        await dial_policy.guard_dial(pool, dial_of(org))
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True))
    await dial_policy.guard_dial(pool, dial_of(org))
    assert await ledger(pool) == [("m_ana", "stranger", None), ("m_ana", None, "call_1")]


@postgres
async def test_a_bad_shape_is_written_down_and_refused_with_its_guard(pool: Pool, org: str) -> None:
    with pytest.raises(DeclarationRefused, match=r"\(shape\)"):
        await dial_policy.guard_dial(pool, dial_of(org, to="+881600000"))
    assert await ledger(pool) == [("m_ana", "shape", None)]


@postgres
async def test_the_pace_counts_the_refusals_too_and_a_days_worth_has_its_own_words(
    pool: Pool, org: str
) -> None:
    await dial_policy.put_guards(
        pool,
        org,
        dial_policy.Guards(dial_anywhere=True, per_minute=2, per_day=100),
    )
    with pytest.raises(DeclarationRefused):
        await dial_policy.guard_dial(pool, dial_of(org, to="bad"))
    await dial_policy.guard_dial(pool, dial_of(org))
    with pytest.raises(QuotaExhausted, match="last minute of its 2 \\(too_fast\\)"):
        await dial_policy.guard_dial(pool, dial_of(org))
    await dial_policy.put_guards(
        pool,
        org,
        dial_policy.Guards(dial_anywhere=True, per_minute=50, per_day=3),
    )
    with pytest.raises(QuotaExhausted, match="today of its 3 \\(too_many\\)"):
        await dial_policy.guard_dial(pool, dial_of(org))


@postgres
async def test_two_dials_at_once_cannot_both_take_the_last_slot(pool: Pool, org: str) -> None:
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True, per_minute=1))
    both = await asyncio.gather(
        dial_policy.guard_dial(pool, dial_of(org, call="call_a")),
        dial_policy.guard_dial(pool, dial_of(org, call="call_b")),
        return_exceptions=True,
    )
    assert sorted(type(item).__name__ for item in both) == ["Guards", "QuotaExhausted"]


@postgres
async def test_a_second_leg_skips_the_stranger_fence_and_the_calls_own_first_leg_passes(
    pool: Pool, org: str
) -> None:
    await dial_policy.guard_second_leg(pool, dial_of(org, to="+34910000000"))
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True, per_minute=2))
    await dial_policy.guard_dial(pool, dial_of(org, call="call_2"))
    await dial_policy.guard_second_leg(pool, dial_of(org, call="call_2"))
    assert len(await ledger(pool)) == 2
