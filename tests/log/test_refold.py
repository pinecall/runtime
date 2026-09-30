"""Tests for the facts folded again from the log: the rebuild, the heads, doctor's sample."""

import asyncio
from typing import LiteralString

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log import facts, refold
from pinecall.log.refold import Differs, Refolding
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from tests.conftest import postgres
from tests.log.conftest import ACall, logged_call

pytestmark = postgres

WRONG = "update call_facts set outcome = 'wrong', e2e = '{}' where call = %(call)s"

FACTS_OF = "select * from call_facts where call = %(call)s"

GONE = "delete from call_facts where call = %(call)s"

HEAD_BACK = "update call_log_head set seq = 1 where log = %(call)s"


async def stored(pool: Pool, call: str) -> facts.CallFacts | None:
    """The facts row as Postgres holds it now."""
    async with pool.connection() as connection:
        row = await (await connection.execute(FACTS_OF, {"call": call})).fetchone()
    return None if row is None else facts.facts_of(row)


async def ran(pool: Pool, statement: LiteralString, call: str) -> None:
    """One statement against the call's rows, as a broken release would have left them."""
    async with pool.connection() as connection:
        await connection.execute(statement, {"call": call})


async def test_a_rebuild_writes_back_what_the_log_folds_to_and_leaves_a_right_row_alone(
    pool: Pool, store: Store, org: str
) -> None:
    broken = await logged_call(store, org)
    right = await logged_call(store, org)
    before = await stored(pool, broken)
    await ran(pool, WRONG, broken)
    done = await refold.rebuild(pool, Refolding())
    assert (done.calls, done.rewritten) == (2, 1)
    assert before is not None
    assert before.outcome == "booked a visit"
    assert await stored(pool, broken) == before
    assert await refold.rebuild(pool, Refolding(call=right)) == refold.Rebuilt(1, 0)


async def test_a_row_lost_is_written_again_and_what_is_not_a_fold_is_kept(
    pool: Pool, store: Store, org: str
) -> None:
    call = await logged_call(store, org)
    before = await stored(pool, call)
    await facts.lent(pool, call, ["acme"])
    await ran(pool, WRONG, call)
    await refold.rebuild(pool, Refolding(call=call))
    async with pool.connection() as connection:
        row = await (await connection.execute(FACTS_OF, {"call": call})).fetchone()
    assert row is not None
    assert row["lent"] == ["acme"]
    await ran(pool, GONE, call)
    assert await refold.rebuild(pool, Refolding(call=call)) == refold.Rebuilt(1, 1)
    assert await stored(pool, call) == before


async def test_a_rebuild_reads_only_the_org_and_the_days_it_is_given_page_by_page(
    pool: Pool, store: Store, org: str
) -> None:
    ours = [await logged_call(store, org) for _ in range(3)]
    theirs = await logged_call(store, "org-other", ACall(scope=Scope("org-other")))
    for call in [*ours, theirs]:
        await ran(pool, WRONG, call)
    assert await refold.rebuild(pool, Refolding(org=org), page=2) == refold.Rebuilt(3, 3)
    assert (await stored(pool, theirs) or facts.CallFacts(call=theirs)).outcome == "wrong"
    assert await refold.rebuild(pool, Refolding(since=10_000.0)) == refold.Rebuilt(0, 0)
    assert await refold.rebuild(pool, Refolding(since=0.0)) == refold.Rebuilt(4, 1)


async def test_appends_that_come_while_their_call_is_refolded_are_folded_all_the_same(
    pool: Pool, store: Store, org: str
) -> None:
    call = await logged_call(store, org, ACall(ended=False))
    heard: JsonObject = {"speech_id": "u", "text": "sigo", "metrics": {}}
    replies = [
        store.append(call, "dental-sur", "turn.user", heard, ephemeral=False) for _ in range(5)
    ]
    await asyncio.gather(refold.rebuild(pool, Refolding(call=call)), *replies)
    folded = await stored(pool, call)
    assert folded is not None
    assert len(folded.heard_at) == 6
    assert await refold.rebuild(pool, Refolding(call=call)) == refold.Rebuilt(1, 0)


async def test_doctor_names_a_head_behind_its_rows_and_a_fact_its_log_does_not_fold_to(
    pool: Pool, store: Store, org: str
) -> None:
    first = await logged_call(store, org)
    second = await logged_call(store, org)
    assert await refold.heads_behind(pool) == refold.Heads(examined=2, behind=())
    assert await refold.differing(pool) == []
    await ran(pool, HEAD_BACK, first)
    await ran(pool, WRONG, second)
    assert await refold.heads_behind(pool) == refold.Heads(examined=2, behind=(first,))
    assert await refold.differing(pool) == [Differs(call=second, columns=("outcome", "e2e"))]
