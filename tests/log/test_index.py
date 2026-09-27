"""Tests for the facts fold and the lists over it, on a real Postgres."""

from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

from pinecall.domain.errors import Conflict
from pinecall.domain.types import Corner, JsonObject
from pinecall.log.index import (
    A_DAY_S,
    CallFacts,
    Inbox,
    Wanted,
    calls_with,
    corner_of_call,
    counted_day,
    ever_reached,
    facts_of_calls,
    fold,
    found,
    lent,
    read,
    runs_of_persona,
    spent_between,
    threads,
    unsealed_spoken,
    unsealed_written,
)
from pinecall.log.store import Claim, Store
from pinecall.postgres.pool import Pool
from pinecall.wire.frames import Entry
from tests.conftest import postgres

pytestmark = postgres

AGENT = "dental-sur"
THE_DAY = 0.0


LENT_OF = "select lent from call_facts where call = %(call)s"


@pytest.fixture
def org() -> str:
    return f"org-{uuid4().hex[:10]}"


def judgment(name: str, verdict: str, reason: str = "") -> JsonObject:
    return {
        "name": name,
        "verdict": verdict,
        "criteria": "c",
        "reason": reason,
        "evidence": {"seqs": []},
    }


def entry(kind: str, data: JsonObject, *, ts: float = 5.0, ephemeral: bool = False) -> Entry:
    return Entry(seq=1, ts=ts, call="CA_x", agent=AGENT, type=kind, ephemeral=ephemeral, data=data)


@dataclass(frozen=True)
class ACall:
    """How a test call went: who called, on which channel, what came of it, what was judged."""

    agent: str = AGENT
    channel: str = "phone"
    caller: str = "+34 600 111 222"
    outcome: str = "booked a visit"
    cost: float = 0.25
    judges: tuple[JsonObject, ...] = ()
    took_over: bool = False
    corner: Corner | None = None
    persona: str | None = None
    ended: bool = True


async def logged_call(store: Store, org: str, went: ACall | None = None) -> str:
    """Write a claimed call the way a session does, and return its id."""
    went = went or ACall()
    agent = went.agent
    call = f"CA_{uuid4().hex[:12]}"
    await store.claim(call, agent, org, Claim(went.corner or Corner(org)))
    line: JsonObject = {
        "channel": went.channel,
        "from": went.caller,
        "to": "+34910000000",
        "caller": None,
    }
    started: JsonObject = {
        **line,
        "direction": "inbound",
        "started_at": 1.0,
        "persona": went.persona,
    }
    await store.append(
        call,
        agent,
        "call.ringing",
        {**line, "route": {"channel": went.channel, "number": "+34910000000"}},
        ephemeral=False,
    )
    await store.append(call, agent, "call.started", started, ephemeral=False)
    await store.append(
        call,
        agent,
        "turn.user",
        {"speech_id": "u1", "text": "hola", "metrics": {}},
        ephemeral=False,
    )
    reply: JsonObject = {
        "speech_id": "a1",
        "text": "buenas",
        "interrupted": False,
        "metrics": {"e2e_latency": 1.5},
    }
    await store.append(call, agent, "turn.agent", reply, ephemeral=False)
    if went.took_over:
        await store.append(
            call, agent, "supervisor.took_over", {"by": {"id": "m_1", "name": "I"}}, ephemeral=False
        )
    if went.ended:
        ended_with: JsonObject = {
            "reason": "caller_hung_up",
            "ended_by": "caller",
            "ended_at": 9.0,
            "duration_s": 8.0,
        }
        await store.append(call, agent, "call.ended", ended_with, ephemeral=False)
        summary: JsonObject = {
            "reason": "caller_hung_up",
            "outcome": went.outcome,
            "duration_s": 8.0,
            "turns": 2,
            "usage": [],
            "cost": {
                "eur": went.cost,
                "rate": {"currency": "EUR", "usd_to_eur": 0.9, "as_of": "2026-09-01"},
                "rows": [],
                "unpriced": [],
            },
        }
        await store.append(call, agent, "call.summary", summary, ephemeral=False)
        score: JsonObject = {"judges": list(went.judges), "judge_calls": 0}
        await store.append(call, agent, "call.score", score, ephemeral=False)
        await store.seal(call)
    return call


# ── the fold, pure ──


def test_an_entry_of_no_call_or_an_ephemeral_one_changes_no_fact() -> None:
    facts = CallFacts(call="CA_x")
    assert (
        fold(
            facts,
            entry("turn.user", {"speech_id": "u", "text": "x", "metrics": {}}, ephemeral=True),
        )
        == facts
    )
    own = entry("agent.registered", {"routes": [], "app": "a"}).model_copy(update={"call": None})
    assert fold(facts, own) == facts


def test_an_entry_nobody_can_read_changes_no_fact_and_raises_nothing() -> None:
    facts = CallFacts(call="CA_x", outcome="kept")
    assert fold(facts, entry("call.summary", {"duration_s": "long"})) == facts
    assert fold(facts, entry("nobody.knows", {})) == facts


def test_a_person_taking_part_escalates_the_call_whatever_the_shape() -> None:
    assert fold(CallFacts(call="CA_x"), entry("supervisor.said", {"anything": 1})).escalated
    assert not fold(
        CallFacts(call="CA_x"), entry("supervisor.whispered", {"anything": 1})
    ).escalated


def test_the_reason_the_call_ended_with_is_kept_over_the_summarys() -> None:
    ended: JsonObject = {
        "reason": "transferred",
        "ended_by": "agent",
        "ended_at": 9.0,
        "duration_s": 8.0,
    }
    facts = fold(CallFacts(call="CA_x"), entry("call.ended", ended))
    assert (facts.end_reason, facts.escalated, facts.ended_at) == ("transferred", True, 9.0)
    summary: JsonObject = {
        "reason": "caller_hung_up",
        "outcome": "o",
        "duration_s": 8.0,
        "turns": 1,
        "usage": [],
        "cost": {
            "eur": 0.1,
            "rate": {"currency": "EUR", "usd_to_eur": 1.0, "as_of": "x"},
            "rows": [],
            "unpriced": [],
        },
    }
    assert fold(facts, entry("call.summary", summary)).end_reason == "transferred"


def test_a_verdict_nobody_settled_is_no_score() -> None:
    skipped = fold(
        CallFacts(call="CA_x"),
        entry("call.score", {"judges": [judgment("consent", "skipped")], "judge_calls": 1}),
    )
    assert (skipped.judged, skipped.held, skipped.passed, skipped.score, skipped.flags) == (
        0,
        0,
        None,
        None,
        [],
    )


def test_a_web_call_is_spoken_once_a_room_opens_and_a_phone_call_always() -> None:
    web = fold(
        CallFacts(call="CA_x"),
        entry(
            "call.started",
            {
                "channel": "web",
                "direction": "inbound",
                "from": "v",
                "to": "a",
                "caller": None,
                "started_at": 1.0,
            },
        ),
    )
    assert not web.spoken
    assert fold(web, entry("room.opened", {"name": "r", "sid": "s", "channel": "web"})).spoken
    phone = fold(
        CallFacts(call="CA_x"),
        entry(
            "call.ringing",
            {
                "channel": "phone",
                "from": "+1",
                "to": "+2",
                "route": {"channel": "phone", "number": "+2"},
                "caller": {"id": "ct_9", "name": "Ana"},
            },
        ),
    )
    assert (phone.spoken, phone.contact, phone.name, phone.direction) == (
        True,
        "ct_9",
        "Ana",
        "inbound",
    )


# ── the fold, at append ──


async def test_append_folds_what_a_list_draws(store: Store, org: str) -> None:
    broken = [
        judgment("consent", "held"),
        judgment("promises", "broken", "a callback nobody booked"),
    ]
    call = await logged_call(store, org, ACall(judges=tuple(broken), took_over=True))
    facts = (await facts_of_calls(store.pool, [call]))[call]
    assert (facts.channel, facts.contact, facts.outcome, facts.cost_eur, facts.agent) == (
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


async def test_folding_a_calls_entries_again_gives_the_row_append_folded(
    store: Store, org: str
) -> None:
    call = await logged_call(
        store, org, ACall(judges=(judgment("consent", "held"),), took_over=True)
    )
    folded = CallFacts(call=call)
    for one in await store.whole(call):
        folded = fold(folded, one)
    assert (await facts_of_calls(store.pool, [call]))[call] == replace(folded, agent=AGENT)


async def test_a_verdict_lands_on_a_sealed_log_and_replaces_the_last_one(
    store: Store, org: str
) -> None:
    call = await logged_call(store, org)
    with pytest.raises(Conflict):
        await store.append(call, AGENT, "turn.user", {"text": "too late"}, ephemeral=False)
    await store.rescored(call, AGENT, {"judges": [judgment("grounded", "held")], "judge_calls": 1})
    assert (await facts_of_calls(store.pool, [call]))[call].score == {
        "held": 1,
        "judged": 1,
        "passed": True,
        "reason": None,
    }


async def test_an_entry_that_changes_no_fact_writes_no_row(
    store: Store, org: str, call: str
) -> None:
    await store.claim(call, AGENT, org)
    await store.append(call, AGENT, "custom", {"name": "n", "data": {}}, ephemeral=False)
    assert await facts_of_calls(store.pool, [call]) == {}


async def test_the_vendors_lent_to_a_call_are_kept_with_its_facts_and_replaced_whole(
    store: Store, pool: Pool, org: str
) -> None:
    call = await logged_call(store, org)
    await lent(pool, call, ["cartesia", "deepgram"])
    await lent(pool, call, ["cartesia"])
    async with pool.connection() as connection:
        row = await (await connection.execute(LENT_OF, {"call": call})).fetchone()
    assert row is not None
    assert row["lent"] == ["cartesia"]
    assert call in await facts_of_calls(pool, [call])


# ── the corner ──


async def test_a_call_nobody_folded_has_no_facts_and_no_corner(pool: Pool) -> None:
    assert await facts_of_calls(pool, ["CA_nobody"]) == {}
    assert await corner_of_call(pool, "CA_nobody") is None


async def test_a_call_says_which_corner_it_was_opened_in(store: Store, org: str) -> None:
    corner = Corner(org, "sandbox", "m_dev")
    live = await logged_call(store, org, ACall(corner=corner, ended=False))
    over = await logged_call(store, org)
    still = await corner_of_call(store.pool, live)
    done = await corner_of_call(store.pool, over)
    assert still is not None
    assert (still.corner, still.agent, still.sealed) == (corner, AGENT, False)
    assert still.started_at is not None
    assert done is not None
    assert (done.corner, done.sealed) == (Corner(org), True)


# ── the lists ──


async def test_a_list_is_found_by_words_numbers_door_and_page(store: Store, org: str) -> None:
    first = await logged_call(
        store, org, ACall(caller="+34 600 111 222", outcome="asked for a price")
    )
    second = await logged_call(store, org, ACall(channel="whatsapp", caller="+34 699 999 000"))
    third = await logged_call(store, org, ACall(agent="another-agent", caller="+34 611 000 000"))
    await logged_call(store, org, ACall(corner=Corner(org, "sandbox", "m_dev")))
    corner = Corner(org)

    async def calls(**asked: str) -> list[str]:
        return (await found(store.pool, corner, Wanted(**asked), limit=10)).calls

    assert await calls() == [third, second, first]
    assert await calls(q="600 111") == [first]
    assert await calls(q="PRICE") == [first]
    assert (await found(store.pool, corner, Wanted(q="ca_"), limit=10)).total == 3
    assert await calls(channel="whatsapp") == [second]
    assert await calls(agent=AGENT) == [second, first]
    one = await found(store.pool, corner, Wanted(), limit=1)
    assert (one.calls, one.total, one.next) == ([third], 3, third)
    rest = await found(store.pool, corner, Wanted(before=one.next), limit=5)
    assert (rest.calls, rest.next) == ([second, first], None)
    assert await calls(q="%") == [], "a percent sign is a character, not a wildcard"


async def test_a_personas_own_runs_are_listed_newest_first_and_paged(
    store: Store, org: str
) -> None:
    older = await logged_call(store, org, ACall(persona="homeowner"))
    newer = await logged_call(store, org, ACall(agent="another-agent", persona="homeowner"))
    await logged_call(store, org, ACall(persona="price-shopper"))
    await logged_call(store, org)
    await logged_call(
        store, org, ACall(persona="homeowner", corner=Corner(org, "sandbox", "m_dev"))
    )
    corner = Corner(org)
    whole = await runs_of_persona(store.pool, corner, "homeowner", before=None, limit=10)
    assert [run.facts.call for run in whole.runs] == [newer, older]
    assert (whole.total, whole.next) == (2, None)
    assert [run.turns for run in whole.runs] == [1, 1]
    first = await runs_of_persona(store.pool, corner, "homeowner", before=None, limit=1)
    assert ([run.facts.call for run in first.runs], first.total, first.next) == ([newer], 2, newer)
    second = await runs_of_persona(store.pool, corner, "homeowner", before=newer, limit=1)
    assert ([run.facts.call for run in second.runs], second.next) == ([older], None)


async def test_a_day_is_counted_in_its_corner(store: Store, org: str) -> None:
    judged = [judgment("consent", "held"), judgment("grounded", "broken", "no")]
    await logged_call(store, org, ACall(judges=tuple(judged), cost=0.5))
    await logged_call(store, org, ACall(took_over=True, cost=0.25, channel="web"))
    await logged_call(store, org, ACall(ended=False))
    await logged_call(store, org, ACall(corner=Corner(org, "sandbox", "m_dev"), cost=4.0))
    day = await counted_day(store.pool, Corner(org), THE_DAY)
    assert (day.calls, day.yesterday, day.finished, day.unescalated) == (3, 0, 2, 1)
    assert (day.spent, day.median_e2e, day.total, day.live) == (0.75, 1.5, 3, 1)
    assert day.channels == {"phone": 2, "web": 1, "whatsapp": 0}
    assert [(one.slug, one.calls, one.score) for one in day.agents] == [(AGENT, 3, 0.5)]
    tomorrow = await counted_day(store.pool, Corner(org), THE_DAY + A_DAY_S)
    assert (tomorrow.calls, tomorrow.yesterday) == (0, 3)
    assert await spent_between(store.pool, org, THE_DAY, 60.0) == 4.75, "a budget spans both worlds"
    assert await spent_between(store.pool, org, 60.0, 120.0) == 0.0


async def test_an_inbox_is_a_line_per_contact_and_counts_what_the_reader_has_not_read(
    store: Store, org: str
) -> None:
    await logged_call(store, org, ACall(channel="whatsapp", caller="+34611", ended=False))
    spoken = await logged_call(store, org, ACall(caller="+34622"))
    written = await logged_call(store, org, ACall(channel="whatsapp", caller="+34611"))
    mine = Inbox(Corner(org), AGENT, "m_1")
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
    theirs = Inbox(Corner(org), AGENT, "m_2")
    assert [
        row.unread for row in (await threads(store.pool, theirs, after=None, limit=10)).rows
    ] == [2, 1]
    assert await calls_with(store.pool, Corner(org), AGENT, "+34622", limit=5) == [spoken]


async def test_a_contact_reached_in_any_env_allows_a_call_back(store: Store, org: str) -> None:
    await logged_call(store, org, ACall(caller="+34633", corner=Corner(org, "sandbox", "m_dev")))
    assert await ever_reached(store.pool, org, "+34633")
    assert not await ever_reached(store.pool, org, "+34644")
    assert not await ever_reached(store.pool, f"{org}-other", "+34633")


# ── the reaper's questions ──


async def test_the_written_calls_still_open_are_answered_with_their_channel(
    store: Store, org: str, call: str
) -> None:
    await store.claim(call, AGENT, org, Claim(Corner(org)))
    started: JsonObject = {
        "channel": "whatsapp",
        "direction": "inbound",
        "from": "+34600",
        "to": "a",
        "caller": None,
        "started_at": 1.0,
    }
    await store.append(call, AGENT, "call.started", started, ephemeral=False)
    spoken = await logged_call(store, org, ACall(ended=False))
    open_now = {one.call: one for one in await unsealed_written(store.pool, 1000.0, limit=500)}
    assert call in open_now
    assert open_now[call].channel == "whatsapp"
    assert spoken not in open_now, "a spoken call is the other question's"


async def test_the_open_spoken_calls_that_have_been_quiet(store: Store, org: str) -> None:
    quiet = await logged_call(store, org, ACall(ended=False))
    written = await logged_call(store, org, ACall(channel="whatsapp", ended=False))
    finished = await logged_call(store, org)
    open_now = {one.call: one for one in await unsealed_spoken(store.pool, 1000.0, limit=500)}
    assert quiet in open_now, "spoken, and not sealed"
    assert written not in open_now, "a written call idles out where it runs"
    assert finished not in open_now, "its log is sealed"
    assert open_now[quiet].agent == AGENT
    assert open_now[quiet].started_at <= open_now[quiet].last_at
    assert quiet not in {one.call for one in await unsealed_spoken(store.pool, 0.0, limit=500)}


async def test_the_quiet_calls_come_oldest_first_and_no_more_than_asked(
    store: Store, org: str
) -> None:
    first = await logged_call(store, org, ACall(ended=False))
    second = await logged_call(store, org, ACall(ended=False))
    answer = await unsealed_spoken(store.pool, 1000.0, limit=500)
    assert [one.last_at for one in answer] == sorted(one.last_at for one in answer)
    assert [one.call for one in answer] == [first, second]
    assert len(await unsealed_spoken(store.pool, 1000.0, limit=1)) == 1
