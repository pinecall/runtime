"""GET /v1/insights: a day of the reader's corner off the call index, and the month's spend."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from starlette.testclient import TestClient

from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Quotas
from pinecall.types.json import JsonObject
from tests.api.conftest import A_KEY, A_RECORD
from tests.api.talking import got

pytestmark = pytest.mark.unit

INSIGHTS = "/v1/insights"
THE_DAY = date(2026, 9, 17)
AN_APP_KEY = "pk_test_holds_agents"
AN_APP = KeyRecord(key_id="k_app", org=A_RECORD.org, scopes=frozenset({"app"}))
THE_SHOPS_KEY = "pk_test_the_shop"
THE_SHOP = KeyRecord(key_id="k_shop", org="tienda")


def at(day: date, hour: int) -> float:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp()


class Clock:
    """A clock a test sets before each call it writes, so a call lands on the day it names."""

    def __init__(self) -> None:
        self.now = at(THE_DAY, 9)

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> MemoryStore:
    return MemoryStore(clock=clock)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, AN_APP_KEY: AN_APP, THE_SHOPS_KEY: THE_SHOP})


async def a_call(
    store: MemoryStore,
    call: str,
    *,
    agent: str = "clinica-norte",
    channel: str = "phone",
    cost: float = 0.1,
    judges: list[JsonObject] | None = None,
    supervised: bool = False,
    ended: bool = True,
) -> None:
    await store.owned(call, agent, A_RECORD.org, "production", "")
    await store.append(call, agent, "call.started", {"channel": channel, "from": "+34600"})
    said = {"text": "hola", "metrics": {"e2e_latency": 0.8}}
    await store.append(call, agent, "turn.agent", said)
    if supervised:
        await store.append(call, agent, "supervisor.ended", {"by": {"id": "m_sup"}})
    if ended:
        await store.append(call, agent, "call.ended", {"reason": "caller_hung_up", "ended_at": 1})
        await store.append(call, agent, "call.summary", {"outcome": "ok", "cost": {"eur": cost}})
        await store.append(call, agent, "call.score", {"judges": judges or [], "judge_calls": 0})
        await store.seal(call)


def verdict(name: str, word: str) -> JsonObject:
    return {"name": name, "verdict": word, "criteria": "", "reason": "", "evidence": {}}


async def test_a_day_is_its_calls_how_they_went_and_what_they_cost(
    gateway: TestClient, store: MemoryStore, clock: Clock, orgs: MemoryOrgs
) -> None:
    await orgs.set_quotas(A_RECORD.org, Quotas(budget_eur=300))
    clock.now = at(date(2026, 9, 16), 10)
    await a_call(store, "CA_yesterday", cost=1.0)
    clock.now = at(THE_DAY, 9)
    held_and_broken = [verdict("consent", "held"), verdict("grounded", "broken")]
    await a_call(store, "CA_judged", judges=held_and_broken, cost=0.5)
    await a_call(store, "CA_taken", channel="whatsapp", supervised=True, agent="tienda-bot")
    await a_call(store, "CA_live", channel="web", ended=False)
    status, body = got(gateway, f"{INSIGHTS}?day={THE_DAY.isoformat()}")
    assert status == 200
    assert body == {
        "day": "2026-09-17",
        "timezone": "UTC",
        "conversations": {"today": 3, "yesterday": 1},
        "resolved_rate": 0.5,
        "median_e2e_s": 0.8,
        "spend_eur": 0.6,
        "channels": {"phone": 1, "web": 1, "whatsapp": 1},
        "sessions_total": 4,
        "live": 1,
        "agents": [
            {"slug": "clinica-norte", "today": 2, "score": 0.5},
            {"slug": "tienda-bot", "today": 1, "score": None},
        ],
        "budget": {"limit_eur": 300, "spent_eur_month": 1.6},
    }


async def test_a_day_with_no_calls_says_nothing_happened_rather_than_zero_rates(
    gateway: TestClient,
) -> None:
    _, body = got(gateway, f"{INSIGHTS}?day=2026-01-01")
    assert (body["resolved_rate"], body["median_e2e_s"], body["agents"]) == (None, None, [])
    assert body["budget"] == {"limit_eur": None, "spent_eur_month": 0.0}


async def test_another_orgs_key_counts_its_own_calls_and_none_of_these(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_clinic")
    _, body = got(gateway, f"{INSIGHTS}?day={THE_DAY.isoformat()}", THE_SHOPS_KEY)
    assert (body["conversations"]["today"], body["sessions_total"]) == (0, 0)


def test_the_door_asks_for_calls(gateway: TestClient) -> None:
    status, body = got(gateway, INSIGHTS, AN_APP_KEY)
    assert (status, body["detail"]) == (403, NOT_OPENED.format(scope="calls", opens="app"))


def test_a_day_that_is_not_a_date_is_refused(gateway: TestClient) -> None:
    assert got(gateway, f"{INSIGHTS}?day=yesterday")[0] == 422
