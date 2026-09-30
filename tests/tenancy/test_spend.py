"""An org that spends strangely: today against its own trailing four weeks, said once a day."""

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import spend
from tests.conftest import postgres
from tests.tenancy.test_usage import AGENT, SEPTEMBER, a_call, a_summary

pytestmark = postgres

A_DAY = 86_400.0
# Yesterday's four calls at seven dollars make the usual day, over four weeks, one dollar.
YESTERDAY = SEPTEMBER - A_DAY
NOON = SEPTEMBER + 12 * 3600


async def test_today_is_unusual_at_three_times_the_usual_day_and_a_new_org_never_is(
    pool: Pool, call: str
) -> None:
    scope = Scope("org-a")
    for n in range(4):
        await a_call(pool, scope, a_summary(1, usd=7.0), at=YESTERDAY, call=f"{call}-{n}")
    await a_call(pool, scope, a_summary(1, usd=2.9), at=SEPTEMBER + 60, call=f"{call}-t1")
    assert await spend.unusual(pool, "org-a", NOON) is None
    await a_call(pool, scope, a_summary(1, usd=0.1), at=SEPTEMBER + 120, call=f"{call}-t2")
    assert await spend.unusual(pool, "org-a", NOON) == spend.Unusual("2026-09-30", 3.0, 1.0, 3.0)
    new = Scope("org-new")
    await a_call(pool, new, a_summary(1, usd=50.0), at=SEPTEMBER + 60, call=f"{call}-new")
    assert await spend.unusual(pool, "org-new", NOON) is None


async def test_it_is_said_once_a_day_for_the_org(pool: Pool) -> None:
    assert not await spend.said_today(pool, "org-a", NOON)
    store = Store(pool, clock=lambda: NOON)
    written: JsonObject = {
        "org": "org-a",
        "day": "2026-09-30",
        "today_usd": 3.0,
        "usual_usd": 1.0,
        "multiple": 3.0,
    }
    await store.append(None, AGENT, "spend.unusual", written, ephemeral=False)
    await store.writer.drained()
    assert await spend.said_today(pool, "org-a", NOON)
    assert not await spend.said_today(pool, "org-b", NOON)
    assert not await spend.said_today(pool, "org-a", NOON + A_DAY)
