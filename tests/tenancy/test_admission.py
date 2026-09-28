"""Admission: a world's limits against what the org's calls in that world summed up."""

import pytest

from pinecall.domain.errors import QuotaExhausted
from pinecall.domain.names import Env, JsonObject
from pinecall.domain.org import Org, Quotas
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim, Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy.admission import (
    Admission,
    admission,
    admit_agent,
    admit_call,
    admit_memory,
    admit_number,
    admit_push,
    admit_seat,
    admit_turn,
    quotas_of,
    set_quotas,
    used,
)
from pinecall.tenancy.orgs import create, remove
from tests.conftest import postgres

CLOSED = Quotas(minutes=0, messages=0, concurrent_calls=0)
TRIAL = Quotas(minutes=30, messages=300, concurrent_calls=1, lends=frozenset({"deepgram"}))


def _summary(seconds: float, turns: int, tokens: int) -> JsonObject:
    llm: JsonObject = {
        "type": "llm_usage",
        "provider": "acme",
        "model": "acme-1",
        "input_tokens": tokens,
        "output_tokens": 0,
    }
    return {
        "reason": "caller_hung_up",
        "outcome": "done",
        "duration_s": seconds,
        "turns": turns,
        "usage": [llm],
        "cost": {
            "eur": 0.01,
            "rate": {"currency": "EUR", "usd_to_eur": 0.9, "as_of": "2026-09-01"},
            "rows": [],
            "unpriced": [],
        },
    }


async def _a_call(store: Store, org: Org, env: Env, summary: JsonObject, call: str) -> None:
    await store.claim(call, "recepcion", org.id, Claim(Scope(org.id, env, "")))
    await store.append(call, "recepcion", "call.summary", summary, ephemeral=False)


@postgres
async def test_what_an_org_used_is_its_calls_summaries_in_that_world_only(
    pool: Pool, store: Store
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    other = await create(pool, "bidfire", "Bidfire")
    await _a_call(store, org, "sandbox", _summary(120, 4, 900), "call_1")
    await _a_call(store, org, "sandbox", _summary(60, 2, 100), "call_2")
    await _a_call(store, org, "production", _summary(600, 10, 5000), "call_3")
    await _a_call(store, other, "sandbox", _summary(600, 10, 5000), "call_4")
    spent = await used(pool, org.id, "sandbox")
    assert (spent.calls, spent.minutes, spent.messages, spent.input_tokens) == (2, 3.0, 6, 1000)


@postgres
async def test_an_org_nobody_limited_is_admitted_with_no_ceiling(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    assert await admit_call(pool, org.id, "sandbox", running=100) is None


@postgres
async def test_a_call_is_admitted_with_what_is_left_of_the_minutes_as_its_ceiling(
    pool: Pool, store: Store
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(minutes=30))
    await _a_call(store, org, "sandbox", _summary(600, 4, 0), "call_1")
    ceiling = await admit_call(pool, org.id, "sandbox", running=0)
    assert ceiling is not None
    assert (ceiling.seconds, ceiling.minutes) == (20 * 60, 30)


@postgres
async def test_the_minutes_spent_refuse_the_next_call_naming_the_quota_and_the_numbers(
    pool: Pool, store: Store
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(minutes=1))
    await _a_call(store, org, "sandbox", _summary(90, 2, 0), "call_1")
    with pytest.raises(
        QuotaExhausted, match=r"used 1\.5 of its 1 minutes in the sandbox"
    ) as refused:
        await admit_call(pool, org.id, "sandbox", running=0)
    assert (refused.value.quota, refused.value.used, refused.value.limit) == ("minutes", 1.5, 1)


@postgres
async def test_what_the_sandbox_spent_never_closes_production(pool: Pool, store: Store) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(minutes=1))
    await set_quotas(pool, org.id, "production", Quotas(minutes=100))
    await _a_call(store, org, "sandbox", _summary(600, 2, 0), "call_1")
    assert await admit_call(pool, org.id, "production", running=0) is not None


@postgres
async def test_a_call_past_the_concurrent_calls_is_refused_before_anything_is_counted(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(concurrent_calls=1))
    await admit_call(pool, org.id, "sandbox", running=0)
    with pytest.raises(QuotaExhausted, match="concurrent calls"):
        await admit_call(pool, org.id, "sandbox", running=1)


@postgres
async def test_the_tokens_spent_refuse_a_call_and_a_turn(pool: Pool, store: Store) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(llm_tokens=1000))
    await _a_call(store, org, "sandbox", _summary(10, 1, 1000), "call_1")
    with pytest.raises(QuotaExhausted, match="llm tokens"):
        await admit_call(pool, org.id, "sandbox", running=0)
    with pytest.raises(QuotaExhausted, match="llm tokens"):
        await admit_turn(pool, org.id, "sandbox", turns=0, tokens=0)


@postgres
async def test_a_long_written_call_counts_its_own_turns_before_its_summary(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(messages=10))
    await admit_turn(pool, org.id, "sandbox", turns=9, tokens=0)
    with pytest.raises(QuotaExhausted, match="10 of its 10 messages"):
        await admit_turn(pool, org.id, "sandbox", turns=10, tokens=0)


@postgres
async def test_the_stocks_are_refused_at_their_limit_and_a_zero_switches_the_feature_off(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    limits = Quotas(agents=2, numbers=0, memory_facts=5, seats=3)
    await set_quotas(pool, org.id, "production", limits)
    await admit_agent(pool, org.id, "production", holding=1)
    with pytest.raises(QuotaExhausted, match="agents"):
        await admit_agent(pool, org.id, "production", holding=2)
    with pytest.raises(QuotaExhausted, match="numbers"):
        await admit_number(pool, org.id, "production", bought=0)
    with pytest.raises(QuotaExhausted, match="memory facts"):
        await admit_memory(pool, org.id, "production", kept=5)
    with pytest.raises(QuotaExhausted, match="seats"):
        await admit_seat(pool, org.id, "production", seated=3)


@postgres
async def test_a_push_that_would_pass_the_chunks_is_refused_whole(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", Quotas(knowledge_chunks=100))
    await admit_push(pool, org.id, "sandbox", keeping=100)
    with pytest.raises(QuotaExhausted, match="101 of its 100 knowledge chunks"):
        await admit_push(pool, org.id, "sandbox", keeping=101)


@postgres
async def test_an_org_nobody_limited_has_no_row_and_no_limits(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    assert await quotas_of(pool, org.id, "production") == Quotas()
    assert await quotas_of(pool, org.id, "sandbox") == Quotas()


@postgres
async def test_every_quota_round_trips_and_zero_comes_back_as_zero_and_not_as_no_limit(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    every = Quotas(
        minutes=0,
        messages=1,
        agents=2,
        concurrent_calls=3,
        memory_facts=4,
        knowledge_chunks=5,
        numbers=6,
        seats=7,
        llm_tokens=8,
        budget_eur=9,
        lends=frozenset({"deepgram", "anthropic/claude-haiku-4-5"}),
    )
    await set_quotas(pool, org.id, "production", every)
    assert await quotas_of(pool, org.id, "production") == every


@postgres
async def test_the_set_is_replaced_whole_so_a_limit_left_out_stops_being_one(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "production", Quotas(minutes=30, seats=3, budget_eur=50))
    await set_quotas(pool, org.id, "production", Quotas(minutes=60))
    assert await quotas_of(pool, org.id, "production") == Quotas(minutes=60)


@postgres
async def test_each_world_keeps_its_own_limits(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", TRIAL)
    await set_quotas(pool, org.id, "production", CLOSED)
    assert await quotas_of(pool, org.id, "sandbox") == TRIAL
    assert await quotas_of(pool, org.id, "production") == CLOSED


@postgres
async def test_removing_the_org_takes_its_quotas_with_it(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "sandbox", TRIAL)
    assert await remove(pool, org.id)
    assert not await remove(pool, org.id)
    assert await quotas_of(pool, org.id, "sandbox") == Quotas()


@postgres
async def test_a_box_that_never_said_what_a_new_org_gets_limits_nothing(pool: Pool) -> None:
    assert await admission(pool) == Admission()
    org = await create(pool, "clinica-norte", "Clínica Norte")
    assert await quotas_of(pool, org.id, "sandbox") == Quotas()
