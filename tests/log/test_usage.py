"""Tests for a scope's window of days in numbers."""

import pytest

from pinecall.domain.scope import Scope
from pinecall.log.facts import A_DAY_S
from pinecall.log.store import Store
from pinecall.log.usage import counted_window
from tests.conftest import postgres
from tests.log.conftest import AGENT, THE_DAY, ACall, judgment, logged_call


@postgres
async def test_a_day_is_counted_in_its_corner(store: Store, org: str) -> None:
    judged = [judgment("consent", "held"), judgment("grounded", "broken", "no")]
    await logged_call(store, org, ACall(judges=tuple(judged), cost=0.5))
    await logged_call(store, org, ACall(took_over=True, cost=0.25, channel="web"))
    await logged_call(store, org, ACall(ended=False))
    await logged_call(store, org, ACall(scope=Scope(org, "sandbox", "m_dev"), cost=4.0))
    day = await counted_window(store.pool, Scope(org), THE_DAY, THE_DAY + A_DAY_S, None)
    assert (day.calls, day.before, day.finished, day.unescalated) == (3, 0, 2, 1)
    assert (day.spent, day.median_e2e, day.total, day.live) == (0.75, 1.5, 3, 1)
    assert (day.judged, day.passed, day.escalated) == (1, 0, 1)
    # The mean length is the minutes the agent's ended calls took, over how many ended.
    assert day.mean_length == pytest.approx(day.agents[0].minutes * 60 / day.finished)
    assert day.channels == {"phone": 2, "web": 1, "whatsapp": 0}
    assert day.endings == [("caller_hung_up", 2)]
    assert [(agent.slug, agent.calls, agent.score) for agent in day.agents] == [(AGENT, 3, 0.5)]
    assert [(row.channels, row.spent, row.judged) for row in day.days] == [
        ({"phone": 2, "web": 1, "whatsapp": 0}, 0.75, 1)
    ]
    tomorrow = await counted_window(
        store.pool, Scope(org), THE_DAY + A_DAY_S, THE_DAY + 2 * A_DAY_S, None
    )
    assert (tomorrow.calls, tomorrow.before) == (0, 3)


@postgres
async def test_a_week_has_a_row_every_day_and_one_agents_is_only_its_calls(
    store: Store, org: str
) -> None:
    await logged_call(store, org, ACall(cost=0.5))
    await logged_call(store, org, ACall(agent="another-agent", cost=2.0))
    week = THE_DAY, THE_DAY + 7 * A_DAY_S
    every = await counted_window(store.pool, Scope(org), *week, None)
    mine = await counted_window(store.pool, Scope(org), *week, AGENT)
    assert (every.calls, every.spent, mine.calls, mine.spent) == (2, 2.5, 1, 0.5)
    assert [row.day.isoformat() for row in mine.days] == [f"1970-01-0{day}" for day in range(1, 8)]
    assert [row.channels["phone"] for row in mine.days] == [1, 0, 0, 0, 0, 0, 0]
    assert [agent.slug for agent in mine.agents] == [AGENT]
    next_week = await counted_window(store.pool, Scope(org), week[1], week[1] + 7 * A_DAY_S, AGENT)
    assert (next_week.calls, next_week.before) == (0, 1)
