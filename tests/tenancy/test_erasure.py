"""Tests for erasure: a call, a contact and an org gone, and the log append-only everywhere else."""

from pathlib import Path

import httpx
import pytest
from psycopg import errors

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.person import KEY_SCOPES
from pinecall.domain.scope import Scope
from pinecall.log import drift
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.process.recordings import Bucket, Disk
from pinecall.tenancy import canary, erasure
from pinecall.tenancy.canary import Canary, CanarySet
from pinecall.wire.scores import CallScore
from tests.conftest import issued, postgres
from tests.fakes.bucket import Bucket as Remote
from tests.log.conftest import AGENT, ACall, judgment, logged_call
from tests.tenancy.conftest import an_org

pytestmark = postgres

ANA = "+34 600 111 222"
LUIS = "+34 600 333 444"

A_FACT = """
INSERT INTO contact_memories (org, env, holder, contact, text, embedding, valid_from, source_call)
VALUES (%(org)s, 'production', '', %(contact)s, %(text)s,
        array_fill(0.1::real, ARRAY[1024])::halfvec, now(), %(call)s)
"""

A_ROOM_TICKET = """
INSERT INTO tokens (call, org, agent, scope, expires_at)
VALUES (%(call)s, %(org)s, %(agent)s, 'talk', now() + interval '1 minute')
"""

LEFT = """
SELECT (SELECT count(*) FROM call_log WHERE log = %(call)s) AS entries,
       (SELECT count(*) FROM call_log_head WHERE log = %(call)s) AS heads,
       (SELECT count(*) FROM call_facts WHERE call = %(call)s) AS facts,
       (SELECT count(*) FROM tokens WHERE call = %(call)s) AS tokens,
       (SELECT count(*) FROM contact_memories WHERE source_call = %(call)s) AS memories,
       (SELECT count(*) FROM drift_calls WHERE call = %(call)s) AS drifted
"""


async def a_fact(pool: Pool, org: str, contact: str, call: str | None) -> None:
    async with pool.connection() as connection:
        params = {"org": org, "contact": contact, "text": "prefers mornings", "call": call}
        await connection.execute(A_FACT, params)


async def left_of(pool: Pool, call: str) -> dict[str, int]:
    async with pool.connection() as connection:
        row = await (await connection.execute(LEFT, {"call": call})).fetchone()
    assert row is not None
    return {name: int(value) for name, value in row.items()}


def a_recording(root: Path, call: str) -> Path:
    directory = root / call
    directory.mkdir(parents=True)
    (directory / "audio.ogg").write_bytes(b"OggS")
    return directory


A_RUN = """
INSERT INTO eval_runs (id, agent, started_at, status, document)
VALUES ('run_1', %s, 1, 'done', '{}')
"""

A_READ = """
INSERT INTO thread_reads (org, env, holder, agent, reader, contact, read_at)
VALUES (%s, 'production', '', %s, 'm_1', '+1', 1)
"""

NOTHING_LEFT = {"entries": 0, "heads": 0, "facts": 0, "tokens": 0, "memories": 0, "drifted": 0}


async def test_a_call_erased_in_a_box_with_a_bucket_leaves_no_object_and_is_counted(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    remote = Remote(objects={f"{org.id}/{call}/audio.ogg": b"OggS", "org_x/CA_x/audio.ogg": b"x"})
    async with httpx.AsyncClient(transport=remote.transport()) as http:
        kept = Bucket(tmp_path, remote.name, http)
        erased = await erasure.call(pool, kept, Scope(org.id), call, by="m_1")
    assert remote.objects == {"org_x/CA_x/audio.ogg": b"x"}
    assert erased.trail.recordings == 1


async def test_a_bucket_that_refuses_leaves_the_call_whole_and_no_trail(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    remote = Remote(objects={f"{org.id}/{call}/audio.ogg": b"OggS"}, refusal=503)
    async with httpx.AsyncClient(transport=remote.transport()) as http:
        with pytest.raises(UpstreamFailed, match="answered 503 to a delete of"):
            await erasure.call(
                pool, Bucket(tmp_path, remote.name, http), Scope(org.id), call, by="m_1"
            )
    assert (await left_of(pool, call))["entries"] > 0
    assert await erasure.trail(pool, org.id) == []


async def test_a_call_erased_leaves_no_row_no_recording_and_a_trail_that_counts_them(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    await a_fact(pool, org.id, ANA, call)
    async with pool.connection() as connection:
        await connection.execute(A_ROOM_TICKET, {"call": call, "org": org.id, "agent": AGENT})
    recording = a_recording(tmp_path, call)
    entries = await store.whole(call)
    assert await drift.fold(pool, call, entries, CallScore.model_validate(entries[-1].data))

    erased = await erasure.call(pool, Disk(tmp_path), Scope(org.id), call, by="m_1")

    assert await left_of(pool, call) == NOTHING_LEFT
    assert not recording.exists()
    assert erased.calls == (call,)
    trail = erased.trail
    assert (trail.what, trail.subject, trail.env, trail.asked_by) == (
        "call",
        call,
        "production",
        "m_1",
    )
    assert (trail.calls, trail.memories, trail.recordings) == (1, 1, 1)
    assert trail.entries > 0


async def test_the_log_still_refuses_every_update_and_a_delete_that_is_not_an_erasure(
    pool: Pool, store: Store
) -> None:
    call = await logged_call(store, "org-x")
    async with pool.connection() as connection:
        with pytest.raises(errors.RestrictViolation):
            await connection.execute("DELETE FROM call_log WHERE log = %s", (call,))
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(erasure.ERASING)
        with pytest.raises(errors.RestrictViolation):
            await connection.execute("UPDATE call_log SET ts = 0 WHERE log = %s", (call,))
    assert (await left_of(pool, call))["entries"] > 0


async def test_a_contacts_erasure_takes_their_calls_and_facts_and_spares_another_contact(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    anas = [await logged_call(store, org.id, ACall(caller=ANA)) for _ in range(2)]
    luis = await logged_call(store, org.id, ACall(caller=LUIS))
    await a_fact(pool, org.id, ANA, anas[0])
    await a_fact(pool, org.id, ANA, None)
    await a_fact(pool, org.id, LUIS, luis)

    erased = await erasure.contact(pool, Disk(tmp_path), Scope(org.id), ANA, by="k_1")

    assert sorted(erased.calls) == sorted(anas)
    assert (erased.trail.what, erased.trail.calls, erased.trail.memories) == ("contact", 2, 2)
    for call in anas:
        assert await left_of(pool, call) == NOTHING_LEFT
    kept = await left_of(pool, luis)
    assert kept["entries"] > 0
    assert kept["memories"] == 1


async def test_a_contact_in_the_other_world_is_not_touched(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id, ACall(caller=ANA))
    erased = await erasure.contact(pool, Disk(tmp_path), Scope(org.id, "sandbox"), ANA, by="k_1")
    assert erased.calls == ()
    assert (await left_of(pool, call))["entries"] > 0


async def test_an_org_erased_takes_every_log_and_its_row_and_its_trail_outlives_it(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    await store.append(None, AGENT, "agent.registered", {"routes": [], "app": "a"}, ephemeral=False)
    await store.claim(None, AGENT, org.id)
    recording = a_recording(tmp_path, call)
    await issued(pool, org.id, "production", KEY_SCOPES)

    async with pool.connection() as connection:
        await connection.execute(A_RUN, (AGENT,))
        await connection.execute(A_READ, (org.id, AGENT))

    erased = await erasure.org(pool, Disk(tmp_path), org.id, by="operator")

    assert erased.calls == (call,)
    assert await left_of(pool, call) == NOTHING_LEFT
    assert not recording.exists()
    async with pool.connection() as connection:
        heads = await (
            await connection.execute(
                "SELECT count(*) AS n FROM call_log_head WHERE org = %s", (org.id,)
            )
        ).fetchone()
        gone = await (
            await connection.execute("SELECT count(*) AS n FROM orgs WHERE id = %s", (org.id,))
        ).fetchone()
        runs = await (await connection.execute("SELECT count(*) AS n FROM eval_runs")).fetchone()
        reads = await (
            await connection.execute("SELECT count(*) AS n FROM thread_reads")
        ).fetchone()
        keys = await (
            await connection.execute("SELECT count(*) AS n FROM api_keys WHERE org = %s", (org.id,))
        ).fetchone()
        records = await (
            await connection.execute(
                "SELECT org, env, from_number FROM call_records WHERE call = %s", (call,)
            )
        ).fetchone()
    assert keys is not None
    assert keys["n"] == 0
    assert records is not None
    assert (records["org"], records["env"], records["from_number"]) == (org.id, "production", ANA)
    assert runs is not None
    assert runs["n"] == 0
    assert reads is not None
    assert reads["n"] == 0
    assert heads is not None
    assert heads["n"] == 0
    assert gone is not None
    assert gone["n"] == 0
    assert [row.what for row in await erasure.trail(pool, org.id)] == ["org"]


async def test_the_trail_is_the_orgs_alone_newest_first(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    other = await an_org(pool, "otra")
    first = await logged_call(store, org.id)
    second = await logged_call(store, org.id)
    theirs = await logged_call(store, other.id)
    await erasure.call(pool, Disk(tmp_path), Scope(org.id), first, by="m_1")
    await erasure.call(pool, Disk(tmp_path), Scope(org.id), second, by="m_1")
    await erasure.call(pool, Disk(tmp_path), Scope(other.id), theirs, by="m_2")
    assert [row.subject for row in await erasure.trail(pool, org.id)] == [second, first]


# Each tenant table keyed by org goes with the org: its foreign key cascades.
ORGS_ROWS = """
SELECT (SELECT count(*) FROM stage_days WHERE org = %(org)s)
     + (SELECT count(*) FROM judge_days WHERE org = %(org)s)
     + (SELECT count(*) FROM drift_calls WHERE org = %(org)s)
     + (SELECT count(*) FROM agent_canaries WHERE org = %(org)s) AS rows
"""


async def orgs_rows(pool: Pool, org: str) -> int:
    """How many rows the org holds in the tables of drift and the canary."""
    async with pool.connection() as connection:
        row = await (await connection.execute(ORGS_ROWS, {"org": org})).fetchone()
    assert row is not None
    return int(row["rows"])


async def test_the_drift_and_the_canary_of_an_org_go_with_it(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id, ACall(judges=(judgment("consent", "held"),)))
    entries = await store.whole(call)
    await drift.fold(pool, call, entries, CallScore.model_validate(entries[-1].data))
    await canary.put(pool, Scope(org.id), AGENT, CanarySet(Canary(1, 10), "m_1", 1.0))
    assert await orgs_rows(pool, org.id) == 3, "a verdict, the call counted, a canary"
    await erasure.org(pool, Disk(tmp_path), org.id, by="operator")
    assert await orgs_rows(pool, org.id) == 0
