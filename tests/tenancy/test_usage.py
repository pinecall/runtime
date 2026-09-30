"""Tests for the usage totals: kept by the database as summaries land, go and move; the refold."""

from datetime import date
from pathlib import Path

import pytest

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.reduce import Usage
from pinecall.log.store import Claim, Store
from pinecall.postgres.pool import Pool
from pinecall.process.recordings import Disk
from pinecall.tenancy import erasure, usage
from pinecall.tenancy.usage import Month
from tests.conftest import postgres

pytestmark = postgres

AGENT = "dental-sur"

# 2026-09-30 and 2026-10-01, UTC: one summary each side of a month's end.
SEPTEMBER = 1790726400.0
OCTOBER = SEPTEMBER + 86400.0


def a_summary(
    minutes: float, *, tokens: int = 0, characters: int = 0, usd: float = 0.0
) -> JsonObject:
    """A summary as the seal writes it, with an LLM's and a TTS's usage."""
    return {
        "reason": "caller_hung_up",
        "outcome": "booked",
        "duration_s": minutes * 60,
        "turns": 3,
        "usage": [
            {
                "type": "llm_usage",
                "provider": "acme",
                "model": "a",
                "input_tokens": tokens,
                "output_tokens": tokens // 2,
            },
            {"type": "llm_usage", "provider": "acme", "model": "b", "input_tokens": None},
            {"type": "tts_usage", "provider": "acme", "model": "v", "characters_count": characters},
            {"type": "stt_usage", "provider": "acme", "model": "e", "input_tokens": 999},
        ],
        "cost": {"usd": usd, "rows": [], "unpriced": []},
    }


async def a_call(pool: Pool, scope: Scope, summary: JsonObject, *, at: float, call: str) -> str:
    """A call claimed in the scope that wrote its summary at that time."""
    store = Store(pool, clock=lambda: at)
    await store.claim(call, AGENT, scope.org, Claim(scope))
    await store.append(call, AGENT, "call.summary", summary, ephemeral=False)
    await store.writer.drained()
    return call


def near(kept: Usage, refolded: Usage) -> bool:
    """The same usage, the minutes and dollars summed in another order."""
    return (
        kept.calls == refolded.calls
        and kept.messages == refolded.messages
        and kept.input_tokens == refolded.input_tokens
        and kept.output_tokens == refolded.output_tokens
        and kept.characters == refolded.characters
        and kept.minutes == pytest.approx(refolded.minutes)
        and kept.cost_usd == pytest.approx(refolded.cost_usd)
    )


async def test_a_summary_is_counted_in_its_org_world_and_month_as_it_is_written(
    pool: Pool, call: str
) -> None:
    scope = Scope("org-a", "sandbox")
    summary = a_summary(2.5, tokens=100, characters=40, usd=0.25)
    await a_call(pool, scope, summary, at=SEPTEMBER, call=call)
    kept = await usage.totals(pool)
    assert list(kept) == [Month("org-a", "sandbox", date(2026, 9, 1))]
    assert kept[Month("org-a", "sandbox", date(2026, 9, 1))] == Usage(
        calls=1,
        minutes=2.5,
        messages=3,
        input_tokens=100,
        output_tokens=50,
        characters=40,
        cost_usd=0.25,
    )
    assert await usage.used(pool, "org-a", "production") == Usage()


async def test_admission_reads_every_month_summed_and_a_month_spend_crosses_both_worlds(
    pool: Pool, call: str
) -> None:
    await a_call(pool, Scope("org-a"), a_summary(1, usd=1.0), at=SEPTEMBER, call=f"{call}-1")
    await a_call(pool, Scope("org-a"), a_summary(2, usd=2.0), at=OCTOBER, call=f"{call}-2")
    await a_call(
        pool, Scope("org-a", "sandbox"), a_summary(4, usd=4.0), at=OCTOBER, call=f"{call}-3"
    )
    used = await usage.used(pool, "org-a", "production")
    assert (used.calls, used.minutes, used.cost_usd) == (2, 3.0, 3.0)
    assert await usage.spent_in(pool, "org-a", date(2026, 10, 17)) == 6.0
    assert await usage.spent_in(pool, "org-a", date(2026, 9, 30)) == 1.0


async def test_an_erased_call_is_taken_out_of_its_orgs_totals(
    pool: Pool, call: str, tmp_path: Path
) -> None:
    scope = Scope("org-a")
    await a_call(pool, scope, a_summary(1, usd=1.0), at=SEPTEMBER, call=f"{call}-kept")
    erased = await a_call(pool, scope, a_summary(2, usd=2.0), at=SEPTEMBER, call=f"{call}-gone")
    await erasure.call(pool, Disk(tmp_path), scope, erased, by="m_1")
    used = await usage.used(pool, "org-a", "production")
    assert (used.calls, used.minutes, used.cost_usd) == (1, 1.0, 1.0)


async def test_a_log_moved_to_another_org_takes_its_usage_along(pool: Pool, call: str) -> None:
    await a_call(pool, Scope("wrong"), a_summary(3), at=SEPTEMBER, call=call)
    assert await Store(pool).moved(AGENT, "right") == 1
    assert (await usage.used(pool, "wrong", "production")).calls == 0
    assert (await usage.used(pool, "right", "production")).minutes == 3.0


async def test_the_refold_writes_what_the_database_kept_and_mends_a_row_that_drifted(
    pool: Pool, call: str
) -> None:
    for n, (scope, at) in enumerate(
        [
            (Scope("org-a"), SEPTEMBER),
            (Scope("org-a"), OCTOBER),
            (Scope("org-b", "sandbox"), OCTOBER),
        ]
    ):
        summary = a_summary(1.1 * (n + 1), tokens=7 * n, characters=n, usd=0.1 * n)
        await a_call(pool, scope, summary, at=at, call=f"{call}-{n}")
    kept = await usage.totals(pool)
    async with pool.connection() as connection:
        await connection.execute("update usage_totals set calls = calls + 5")
    refolded = await usage.rebuild(pool)
    assert sorted(refolded) == sorted(kept)
    assert all(near(kept[month], refolded[month]) for month in kept)
    assert all(near(row, refolded[month]) for month, row in (await usage.totals(pool)).items())
