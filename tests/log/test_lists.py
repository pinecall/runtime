"""Tests for the pages of calls a list asks for, and a persona's runs."""

from dataclasses import replace

from pinecall.domain.scope import Scope
from pinecall.log.lists import ListFilters, PersonaRunFilters, found, runs_of_persona
from pinecall.log.store import Store
from tests.conftest import postgres
from tests.log.conftest import AGENT, ACall, logged_call


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
    newer = await logged_call(store, org, ACall(persona="homeowner"))
    await logged_call(store, org, ACall(agent="another-agent", persona="homeowner"))
    await logged_call(store, org, ACall(persona="price-shopper"))
    await logged_call(store, org)
    await logged_call(store, org, ACall(persona="homeowner", scope=Scope(org, "sandbox", "m_dev")))
    scope = Scope(org)
    homeowner = PersonaRunFilters(agent=AGENT, persona="homeowner")
    whole = await runs_of_persona(store.pool, scope, homeowner, limit=10)
    assert [run.facts.call for run in whole.runs] == [newer, older]
    assert (whole.total, whole.next) == (2, None)
    assert [run.turns for run in whole.runs] == [1, 1]
    first = await runs_of_persona(store.pool, scope, homeowner, limit=1)
    assert ([run.facts.call for run in first.runs], first.total, first.next) == ([newer], 2, newer)
    after = replace(homeowner, before=newer)
    second = await runs_of_persona(store.pool, scope, after, limit=1)
    assert ([run.facts.call for run in second.runs], second.next) == ([older], None)


@postgres
async def test_a_personas_runs_are_the_agents_it_called_and_no_other_agents(
    store: Store, org: str
) -> None:
    mine = await logged_call(store, org, ACall(persona="homeowner"))
    theirs = await logged_call(store, org, ACall(agent="another-agent", persona="homeowner"))
    scope = Scope(org)
    for agent, call in ((AGENT, mine), ("another-agent", theirs)):
        wanted = PersonaRunFilters(agent=agent, persona="homeowner")
        found = await runs_of_persona(store.pool, scope, wanted, limit=10)
        assert ([run.facts.call for run in found.runs], found.total) == ([call], 1)


@postgres
async def test_no_agent_and_no_persona_named_is_every_simulated_call_and_no_persons(
    store: Store, org: str
) -> None:
    ours = await logged_call(store, org, ACall(persona="homeowner"))
    theirs = await logged_call(store, org, ACall(agent="another-agent", persona="painter"))
    await logged_call(store, org, ACall())
    found = await runs_of_persona(store.pool, Scope(org), PersonaRunFilters(), limit=10)
    assert ([run.facts.call for run in found.runs], found.total) == ([theirs, ours], 2)
