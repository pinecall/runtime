"""Tests for the lists over every call's facts."""

from pinecall.domain.scope import Scope
from pinecall.log.facts import A_DAY_S
from pinecall.log.queries import (
    Inbox,
    ListFilters,
    calls_with,
    counted_day,
    facts_of_calls,
    found,
    read,
    runs_of_persona,
    spent_between,
    threads,
)
from pinecall.log.store import Store
from tests.conftest import postgres
from tests.log.conftest import AGENT, THE_DAY, ACall, judgment, logged_call


@postgres
async def test_append_folds_what_a_list_draws(store: Store, org: str) -> None:
    broken = [
        judgment("consent", "held"),
        judgment("promises", "broken", "a callback nobody booked"),
    ]
    call = await logged_call(store, org, ACall(judges=tuple(broken), took_over=True))
    facts = (await facts_of_calls(store.pool, [call]))[call]
    assert (facts.channel, facts.contact, facts.outcome, facts.cost_usd, facts.agent) == (
        "phone",
        "+34 600 111 222",
        "booked a visit",
        0.25,
        AGENT,
    )
    assert facts.score == {
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
    assert facts.heard_at != ()


@postgres
async def test_a_list_is_found_by_words_numbers_door_and_page(store: Store, org: str) -> None:
    first = await logged_call(
        store, org, ACall(caller="+34 600 111 222", outcome="asked for a price")
    )
    second = await logged_call(store, org, ACall(channel="whatsapp", caller="+34 699 999 000"))
    third = await logged_call(store, org, ACall(agent="another-agent", caller="+34 611 000 000"))
    await logged_call(store, org, ACall(scope=Scope(org, "sandbox", "m_dev")))
    scope = Scope(org)

    async def calls(**asked: str) -> list[str]:
        return (await found(store.pool, scope, ListFilters(**asked), limit=10)).calls

    assert await calls() == [third, second, first]
    assert await calls(q="600 111") == [first]
    assert await calls(q="PRICE") == [first]
    assert (await found(store.pool, scope, ListFilters(q="ca_"), limit=10)).total == 3
    assert await calls(channel="whatsapp") == [second]
    assert await calls(agent=AGENT) == [second, first]
    item = await found(store.pool, scope, ListFilters(), limit=1)
    assert (item.calls, item.total, item.next) == ([third], 3, third)
    rest = await found(store.pool, scope, ListFilters(before=item.next), limit=5)
    assert (rest.calls, rest.next) == ([second, first], None)
    assert await calls(q="%") == [], "a percent sign is a character, not a wildcard"


@postgres
async def test_a_personas_own_runs_are_listed_newest_first_and_paged(
    store: Store, org: str
) -> None:
    older = await logged_call(store, org, ACall(persona="homeowner"))
    newer = await logged_call(store, org, ACall(agent="another-agent", persona="homeowner"))
    await logged_call(store, org, ACall(persona="price-shopper"))
    await logged_call(store, org)
    await logged_call(store, org, ACall(persona="homeowner", scope=Scope(org, "sandbox", "m_dev")))
    scope = Scope(org)
    whole = await runs_of_persona(store.pool, scope, "homeowner", before=None, limit=10)
    assert [run.facts.call for run in whole.runs] == [newer, older]
    assert (whole.total, whole.next) == (2, None)
    assert [run.turns for run in whole.runs] == [1, 1]
    first = await runs_of_persona(store.pool, scope, "homeowner", before=None, limit=1)
    assert ([run.facts.call for run in first.runs], first.total, first.next) == ([newer], 2, newer)
    second = await runs_of_persona(store.pool, scope, "homeowner", before=newer, limit=1)
    assert ([run.facts.call for run in second.runs], second.next) == ([older], None)


@postgres
async def test_a_day_is_counted_in_its_corner(store: Store, org: str) -> None:
    judged = [judgment("consent", "held"), judgment("grounded", "broken", "no")]
    await logged_call(store, org, ACall(judges=tuple(judged), cost=0.5))
    await logged_call(store, org, ACall(took_over=True, cost=0.25, channel="web"))
    await logged_call(store, org, ACall(ended=False))
    await logged_call(store, org, ACall(scope=Scope(org, "sandbox", "m_dev"), cost=4.0))
    day = await counted_day(store.pool, Scope(org), THE_DAY)
    assert (day.calls, day.yesterday, day.finished, day.unescalated) == (3, 0, 2, 1)
    assert (day.spent, day.median_e2e, day.total, day.live) == (0.75, 1.5, 3, 1)
    assert day.channels == {"phone": 2, "web": 1, "whatsapp": 0}
    assert [(agent.slug, agent.calls, agent.score) for agent in day.agents] == [(AGENT, 3, 0.5)]
    tomorrow = await counted_day(store.pool, Scope(org), THE_DAY + A_DAY_S)
    assert (tomorrow.calls, tomorrow.yesterday) == (0, 3)
    assert await spent_between(store.pool, org, THE_DAY, 60.0) == 4.75, "a budget spans both worlds"
    assert await spent_between(store.pool, org, 60.0, 120.0) == 0.0


@postgres
async def test_an_inbox_is_a_line_per_contact_and_counts_what_the_reader_has_not_read(
    store: Store, org: str
) -> None:
    await logged_call(store, org, ACall(channel="whatsapp", caller="+34611", ended=False))
    spoken = await logged_call(store, org, ACall(caller="+34622"))
    written = await logged_call(store, org, ACall(channel="whatsapp", caller="+34611"))
    mine = Inbox(Scope(org), AGENT, "m_1")
    inbox = await threads(store.pool, mine, after=None, limit=10)
    assert [(row.contact, row.calls, row.unread) for row in inbox.rows] == [
        ("+34611", 2, 2),
        ("+34622", 1, 1),
    ]
    assert inbox.rows[0].newest.call == written
    first = await threads(store.pool, mine, after=None, limit=1)
    assert [row.contact for row in first.rows] == ["+34611"]
    after = await threads(store.pool, mine, after=first.next, limit=1)
    assert ([row.contact for row in after.rows], after.next) == (["+34622"], None)
    await read(store.pool, mine, "+34611", 10_000.0)
    assert [row.unread for row in (await threads(store.pool, mine, after=None, limit=10)).rows] == [
        0,
        1,
    ]
    theirs = Inbox(Scope(org), AGENT, "m_2")
    assert [
        row.unread for row in (await threads(store.pool, theirs, after=None, limit=10)).rows
    ] == [2, 1]
    assert await calls_with(store.pool, Scope(org), AGENT, "+34622", limit=5) == [spoken]
