"""Tests for the export: an org's world whole as JSON Lines, and nothing of another org or world."""

import json

import pytest

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import export
from tests.conftest import postgres
from tests.log.conftest import ACall, logged_call
from tests.tenancy.conftest import an_org

pytestmark = postgres

A_FACT = """
INSERT INTO contact_memories (org, env, holder, contact, text, embedding, valid_from)
VALUES (%(org)s, %(env)s, '', '+34 600 111 222', %(text)s,
        array_fill(0.1::real, ARRAY[1024])::halfvec, now())
"""

A_BASE = """
INSERT INTO knowledge_bases (org, env, holder, base, model, dimensions, chunks)
VALUES (%(org)s, 'production', '', 'clinic', 'embedder', 1024, 1)
"""

A_DOCUMENT = """
INSERT INTO knowledge_files (org, env, holder, base, path, text, chunks)
VALUES (%(org)s, 'production', '', 'clinic', 'hours.md', 'Open 9 to 5.', 1)
"""


async def exported(pool: Pool, org: str, env: str = "production") -> list[JsonObject]:
    world = "sandbox" if env == "sandbox" else "production"
    return [json.loads(line) async for line in export.lines(pool, org, world)]


async def test_an_orgs_world_comes_out_whole_header_first(pool: Pool, store: Store) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    async with pool.connection() as connection:
        await connection.execute(A_FACT, {"org": org.id, "env": "production", "text": "mornings"})
        await connection.execute(A_BASE, {"org": org.id})
        await connection.execute(A_DOCUMENT, {"org": org.id})

    lines = await exported(pool, org.id)

    assert (lines[0]["kind"], lines[0]["org"], lines[0]["env"]) == ("export", org.id, "production")
    kinds = [line["kind"] for line in lines]
    assert kinds == ["export", "call", "memory", "knowledge_file"]
    whole = lines[1]
    assert whole["call"] == call
    entries = whole["entries"]
    assert isinstance(entries, list)
    assert [entry["type"] for entry in entries if isinstance(entry, dict)][:2] == [
        "call.ringing",
        "call.started",
    ]
    facts = whole["facts"]
    assert isinstance(facts, dict)
    assert facts["contact"] == "+34 600 111 222"
    assert "embedding" not in lines[2]
    assert lines[3]["text"] == "Open 9 to 5."


async def test_another_org_and_the_other_world_are_not_in_it(pool: Pool, store: Store) -> None:
    org = await an_org(pool)
    other = await an_org(pool, "otra")
    await logged_call(store, other.id)
    await logged_call(store, org.id, ACall(scope=Scope(org.id, "sandbox")))
    assert [line["kind"] for line in await exported(pool, org.id)] == ["export"]
    assert [line["kind"] for line in await exported(pool, org.id, "sandbox")] == ["export", "call"]


async def test_the_calls_come_a_page_at_a_time_and_none_twice(
    pool: Pool, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "A_PAGE_OF_CALLS", 2)
    org = await an_org(pool)
    calls = {await logged_call(store, org.id) for _ in range(5)}
    lines = await exported(pool, org.id)
    listed = [str(line["call"]) for line in lines if line["kind"] == "call"]
    assert sorted(listed) == sorted(calls)
