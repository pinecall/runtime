"""The call index, against both stores: what append folds, and the questions asked across calls."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

import pytest

from pinecall.log.facts import CallFacts
from pinecall.log.store import LogSealed, PostgresStore
from pinecall.log.store.index import CallIndex, Wanted
from pinecall.log.store.postgres import MIGRATIONS
from pinecall.types.json import JsonObject

BACKENDS = [
    pytest.param("memory", marks=pytest.mark.unit),
    pytest.param("postgres", marks=pytest.mark.postgres),
]

# Every clock of the suite starts at 1.0 and ticks halves, so every call lands in the day at 0.
THE_DAY = 0.0

BACKFILL = "0026_call_facts_backfill.post.sql"


class Indexing(CallIndex, Protocol):
    """A store as this suite uses it: it appends, claims, seals, and answers the index."""

    async def append(
        self, call: str | None, agent: str, type: str, data: JsonObject, ephemeral: bool = False
    ) -> Any: ...

    async def owned(
        self,
        call: str | None,
        agent: str,
        org: str,
        env: str | None = None,
        holder: str | None = None,
    ) -> None: ...

    async def seal(self, call: str) -> None: ...

    async def rescored(self, call: str, agent: str, data: JsonObject) -> Any: ...


@pytest.fixture(params=BACKENDS)
def store(request: pytest.FixtureRequest) -> Indexing:
    """Both stores, typed as what this suite asks of them."""
    return request.getfixturevalue(f"{request.param}_store")


@pytest.fixture
def org() -> str:
    """An org nobody else in the run's schema has written calls for."""
    return f"org-{uuid4().hex[:10]}"


def a_judge(name: str, verdict: str, reason: str = "") -> JsonObject:
    """One judgment as call.score carries it."""
    return {
        "name": name,
        "verdict": verdict,
        "criteria": "c",
        "reason": reason,
        "evidence": {"seqs": []},
    }


async def a_call(
    store: Indexing,
    org: str,
    agent: str,
    *,
    channel: str = "phone",
    caller: str = "+34 600 111 222",
    outcome: str = "booked a visit",
    cost: float = 0.25,
    judges: list[JsonObject] | None = None,
    took_over: bool = False,
    env: str = "production",
    holder: str = "",
    ended: bool = True,
) -> str:
    """One call as a session writes it, claimed by the org, and its id."""
    call = f"CA_{uuid4().hex[:12]}"
    await store.owned(call, agent, org, env, holder)
    line = {"channel": channel, "from": caller, "to": "+34910000000", "caller": None}
    await store.append(call, agent, "call.ringing", line)
    await store.append(
        call, agent, "call.started", {**line, "direction": "inbound", "started_at": 1}
    )
    await store.append(call, agent, "turn.user", {"text": "hola"})
    await store.append(
        call, agent, "turn.agent", {"text": "buenas", "metrics": {"e2e_latency": 1.5}}
    )
    if took_over:
        await store.append(call, agent, "supervisor.took_over", {"by": {"id": "m_1"}})
    if ended:
        await store.append(call, agent, "call.ended", {"reason": "caller_hung_up", "ended_at": 9})
        summary = {"reason": "caller_hung_up", "outcome": outcome, "cost": {"eur": cost}}
        await store.append(call, agent, "call.summary", summary)
        score: JsonObject = {"judges": judges or [], "judge_calls": 0}
        await store.append(call, agent, "call.score", score)
        await store.seal(call)
    return call


async def test_append_folds_what_a_list_draws(store: Indexing, org: str, agent: str) -> None:
    broken = [a_judge("consent", "held"), a_judge("promises", "broken", "a callback nobody booked")]
    call = await a_call(store, org, agent, judges=broken, took_over=True)
    facts = (await store.facts_of([call]))[call]
    assert (facts.channel, facts.contact, facts.outcome, facts.cost_eur) == (
        "phone",
        "+34 600 111 222",
        "booked a visit",
        0.25,
    )
    assert facts.score_row == {
        "held": 1,
        "judged": 2,
        "passed": False,
        "reason": "a callback nobody booked",
    }
    assert facts.flags == ["escalated", "low_score", "promise"]
    assert (facts.e2e, facts.spoken, facts.last_text, facts.last_in) == (
        (1.5,),
        True,
        "buenas",
        False,
    )


async def test_a_verdict_lands_on_a_sealed_log_and_replaces_the_last_one(
    store: Indexing, org: str, agent: str
) -> None:
    call = await a_call(store, org, agent)
    with pytest.raises(LogSealed):
        await store.append(call, agent, "turn.user", {"text": "too late"})
    await store.rescored(call, agent, {"judges": [a_judge("grounded", "held")], "judge_calls": 1})
    assert (await store.facts_of([call]))[call].score_row == {
        "held": 1,
        "judged": 1,
        "passed": True,
        "reason": None,
    }


async def test_a_list_is_found_by_words_numbers_door_and_page(
    store: Indexing, org: str, agent: str
) -> None:
    first = await a_call(store, org, agent, caller="+34 600 111 222", outcome="asked for a price")
    second = await a_call(store, org, agent, channel="whatsapp", caller="+34 699 999 000")
    third = await a_call(store, org, "another-agent", caller="+34 611 000 000")
    await a_call(store, org, agent, env="sandbox", holder="m_dev")

    def found(**asked: Any) -> Any:
        return store.found(org, "production", "", Wanted(**asked), 10)

    assert (await found()).calls == [third, second, first]
    assert (await found(q="600 111")).calls == [first]
    assert (await found(q="PRICE")).calls == [first]
    assert (await found(q="ca_")).total == 3, "the start every id shares matches them all"
    assert (await found(channel="whatsapp")).calls == [second]
    assert (await found(agent=agent)).calls == [second, first]
    one = await store.found(org, "production", "", Wanted(), 1)
    assert (one.calls, one.total, one.next) == ([third], 3, third)
    rest = await store.found(org, "production", "", Wanted(before=one.next), 5)
    assert (rest.calls, rest.next) == ([second, first], None)
    assert (await found(q="%")).calls == [], "a percent sign is a character, not a wildcard"


async def test_a_day_is_counted_in_its_corner(store: Indexing, org: str, agent: str) -> None:
    judged = [a_judge("consent", "held"), a_judge("grounded", "broken", "no")]
    await a_call(store, org, agent, judges=judged, cost=0.5)
    await a_call(store, org, agent, took_over=True, cost=0.25, channel="web")
    await a_call(store, org, agent, ended=False)
    await a_call(store, org, agent, env="sandbox", holder="m_dev", cost=4.0)
    day = await store.a_day(org, "production", "", THE_DAY)
    assert (day.calls, day.yesterday, day.finished, day.unescalated) == (3, 0, 2, 1)
    assert (day.spent, day.median_e2e, day.total, day.live) == (0.75, 1.5, 3, 1)
    assert day.channels == {"phone": 2, "web": 1, "whatsapp": 0}
    assert [(one.slug, one.calls, one.score) for one in day.agents] == [(agent, 3, 0.5)]
    tomorrow = await store.a_day(org, "production", "", THE_DAY + 24 * 60 * 60)
    assert (tomorrow.calls, tomorrow.yesterday) == (0, 3)
    assert await store.spent_between(org, THE_DAY, 60.0) == 4.75, "a budget spans both worlds"
    assert await store.spent_between(org, 60.0, 120.0) == 0.0


async def test_an_inbox_is_a_line_per_contact_and_counts_what_the_reader_has_not_read(
    store: Indexing, org: str, agent: str
) -> None:
    await a_call(store, org, agent, channel="whatsapp", caller="+34611", ended=False)
    spoken = await a_call(store, org, agent, caller="+34622")
    written = await a_call(store, org, agent, channel="whatsapp", caller="+34611")
    inbox = await store.threads(org, "production", "", agent, "m_1", None, 10)
    assert [(row.contact, row.calls, row.unread) for row in inbox.rows] == [
        ("+34611", 2, 2),
        ("+34622", 1, 1),
    ]
    assert inbox.rows[0].newest.call == written
    first = await store.threads(org, "production", "", agent, "m_1", None, 1)
    assert [row.contact for row in first.rows] == ["+34611"]
    after = await store.threads(org, "production", "", agent, "m_1", first.next, 1)
    assert ([row.contact for row in after.rows], after.next) == (["+34622"], None)
    await store.read(org, "production", "", agent, "m_1", "+34611", 10_000.0)
    read = await store.threads(org, "production", "", agent, "m_1", None, 10)
    assert [row.unread for row in read.rows] == [0, 1]
    other = await store.threads(org, "production", "", agent, "m_2", None, 10)
    assert [row.unread for row in other.rows] == [2, 1], "a cursor is one person's"
    assert await store.calls_with(org, "production", "", agent, "+34622", 5) == [spoken]


@pytest.mark.postgres
async def test_the_backfill_folds_an_old_call_to_the_very_facts_append_folds(
    postgres_store: PostgresStore, raw_connection: Any, org: str, agent: str
) -> None:
    """0026 restates log/facts.py in SQL for calls from before 0025: the two must agree."""
    judges = [a_judge("consent", "held"), a_judge("promises", "broken", "a visit nobody booked")]
    spoken = await a_call(postgres_store, org, agent, judges=judges, took_over=True)
    written = await a_call(postgres_store, org, agent, channel="whatsapp", ended=False)
    folded = await postgres_store.facts_of([spoken, written])
    await raw_connection.execute("delete from call_facts where call = any($1)", [spoken, written])
    await raw_connection.execute((MIGRATIONS / BACKFILL).read_text(encoding="utf-8"))
    assert await postgres_store.facts_of([spoken, written]) == folded


async def test_a_call_nobody_folded_has_no_facts_and_no_corner(store: Indexing) -> None:
    assert await store.facts_of(["CA_nobody"]) == {}
    assert await store.corner_of_call("CA_nobody") is None


async def test_a_call_says_which_corner_it_was_opened_in(
    store: Indexing, org: str, agent: str
) -> None:
    call = await a_call(store, org, agent, env="sandbox", holder="m_dev")
    corner = await store.corner_of_call(call)
    assert corner is not None and corner.is_in(org, "sandbox", "m_dev") and corner.agent == agent


def test_a_verdict_nobody_settled_is_no_score() -> None:
    skipped = CallFacts(call="CA_1", judged=0, held=0, passed=None)
    assert (skipped.score_row, skipped.flags) == (None, [])


# The reaper's one question (api/reaping.py): which spoken calls this store never finished writing.
# It is asked of every org at once, so a test says which of the answer is its own — the postgres
# schema is one pytest process's and holds whatever the tests before it left open.
async def test_the_open_spoken_calls_that_have_been_quiet(
    store: Indexing, org: str, agent: str
) -> None:
    quiet = await a_call(store, org, agent, ended=False)
    written = await a_call(store, org, agent, channel="whatsapp", ended=False)
    finished = await a_call(store, org, agent)
    # Every entry of this suite's clock is before 1000.0, so every open call is quiet by then.
    open_now = {one.call: one for one in await store.unsealed_spoken(1000.0, 500)}
    assert quiet in open_now, "spoken, and not sealed"
    assert written not in open_now, "a written call idles out where it runs"
    assert finished not in open_now, "its log is sealed"
    assert open_now[quiet].agent == agent
    assert open_now[quiet].started_at <= open_now[quiet].last_at
    assert quiet not in {one.call for one in await store.unsealed_spoken(0.0, 500)}


async def test_the_quiet_calls_come_oldest_first_and_no_more_than_asked(
    store: Indexing, org: str, agent: str
) -> None:
    first = await a_call(store, org, agent, ended=False)
    second = await a_call(store, org, agent, ended=False)
    answer = await store.unsealed_spoken(1000.0, 500)
    assert [one.last_at for one in answer] == sorted(one.last_at for one in answer)
    assert [one.call for one in answer if one.call in {first, second}] == [first, second]
    assert len(await store.unsealed_spoken(1000.0, 1)) == 1, "no more than asked for"
