"""Tests for the facts fold and the lists over it, on a real Postgres."""

from dataclasses import replace

import pytest

from pinecall.domain.errors import Conflict
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.facts import CallFacts, fold, lent
from pinecall.log.queries import (
    ever_reached,
    facts_of_calls,
    scope_of_call,
    unsealed_spoken,
    unsealed_written,
)
from pinecall.log.store import Claim, Store, Unnumbered
from pinecall.postgres.pool import Pool
from tests.conftest import postgres
from tests.log.conftest import AGENT, ACall, entry, judgment, logged_call
from tests.wire.golden import golden_entries

pytestmark = postgres


LENT_OF = "select lent from call_facts where call = %(call)s"

# What a reader's stream carries and no writer writes.
MARKERS = frozenset({"log.gap", "log.caught_up"})

# What the gateway writes on a call's log itself; the rest of the golden is the worker's.
GATEWAYS = frozenset(
    {
        "call.ringing",
        "call.started",
        "tool.call",
        "tool.result",
        "memory.ops",
        "docs.sources",
        "call.summary",
        "call.score",
    }
)


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
            "usd": 0.1,
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


async def test_folding_a_calls_entries_again_gives_the_row_append_folded(
    store: Store, org: str
) -> None:
    call = await logged_call(
        store, org, ACall(judges=(judgment("consent", "held"),), took_over=True)
    )
    folded = CallFacts(call=call)
    for item in await store.whole(call):
        folded = fold(folded, item)
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


async def test_a_batch_folds_to_the_row_its_entries_fold_to_one_by_one(
    store: Store, org: str
) -> None:
    one_by_one, batched = f"CA_{org}_single", f"CA_{org}_batched"
    for call in (one_by_one, batched):
        await store.claim(call, AGENT, org, Claim(Scope(org)))
    pending: list[Unnumbered] = []
    taken = 0
    for item in golden_entries():
        if item.type in MARKERS:
            continue
        single = await store.append(
            one_by_one, item.agent, item.type, item.data, ephemeral=item.ephemeral
        )
        if item.type not in GATEWAYS:
            # The batch carries the times the single path was stamped with.
            pending.append(
                Unnumbered(type=item.type, data=item.data, ephemeral=item.ephemeral, ts=single.ts)
            )
            continue
        if pending:
            await store.append_many(batched, item.agent, pending, after=taken)
            taken, pending = taken + len(pending), []
        await store.append(batched, item.agent, item.type, item.data, ephemeral=item.ephemeral)
    rows = await facts_of_calls(store.pool, [one_by_one, batched])
    assert rows[one_by_one].ended_at is not None
    assert rows[one_by_one].heard_at
    assert replace(rows[batched], call=one_by_one) == rows[one_by_one]


# ── the scope ──


async def test_a_call_nobody_folded_has_no_facts_and_no_corner(pool: Pool) -> None:
    assert await facts_of_calls(pool, ["CA_nobody"]) == {}
    assert await scope_of_call(pool, "CA_nobody") is None


async def test_a_call_says_which_corner_it_was_opened_in(store: Store, org: str) -> None:
    scope = Scope(org, "sandbox", "m_dev")
    live = await logged_call(store, org, ACall(scope=scope, ended=False))
    over = await logged_call(store, org)
    still = await scope_of_call(store.pool, live)
    done = await scope_of_call(store.pool, over)
    assert still is not None
    assert (still.scope, still.agent, still.sealed) == (scope, AGENT, False)
    assert still.started_at is not None
    assert done is not None
    assert (done.scope, done.sealed) == (Scope(org), True)


# ── the lists ──


async def test_a_contact_reached_in_a_world_allows_a_call_back_in_that_world_alone(
    store: Store, org: str
) -> None:
    await logged_call(store, org, ACall(caller="+34633", scope=Scope(org, "sandbox", "m_dev")))
    assert await ever_reached(store.pool, org, "sandbox", "+34633")
    assert not await ever_reached(store.pool, org, "production", "+34633")
    assert not await ever_reached(store.pool, org, "sandbox", "+34644")
    assert not await ever_reached(store.pool, f"{org}-other", "sandbox", "+34633")


# ── the reaper's questions ──


async def test_the_written_calls_still_open_are_answered_with_their_channel(
    store: Store, org: str, call: str
) -> None:
    await store.claim(call, AGENT, org, Claim(Scope(org)))
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
    open_now = {item.call: item for item in await unsealed_written(store.pool, 1000.0, limit=500)}
    assert call in open_now
    assert open_now[call].channel == "whatsapp"
    assert spoken not in open_now, "a spoken call is the other question's"


async def test_the_open_spoken_calls_that_have_been_quiet(store: Store, org: str) -> None:
    quiet = await logged_call(store, org, ACall(ended=False))
    written = await logged_call(store, org, ACall(channel="whatsapp", ended=False))
    finished = await logged_call(store, org)
    open_now = {item.call: item for item in await unsealed_spoken(store.pool, 1000.0, limit=500)}
    assert quiet in open_now, "spoken, and not sealed"
    assert written not in open_now, "a written call idles out where it runs"
    assert finished not in open_now, "its log is sealed"
    assert open_now[quiet].agent == AGENT
    assert open_now[quiet].started_at <= open_now[quiet].last_at
    assert quiet not in {item.call for item in await unsealed_spoken(store.pool, 0.0, limit=500)}


async def test_the_quiet_calls_come_oldest_first_and_no_more_than_asked(
    store: Store, org: str
) -> None:
    first = await logged_call(store, org, ACall(ended=False))
    second = await logged_call(store, org, ACall(ended=False))
    answer = await unsealed_spoken(store.pool, 1000.0, limit=500)
    assert [item.last_at for item in answer] == sorted(item.last_at for item in answer)
    assert [item.call for item in answer] == [first, second]
    assert len(await unsealed_spoken(store.pool, 1000.0, limit=1)) == 1
