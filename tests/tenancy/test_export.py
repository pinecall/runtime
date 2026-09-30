"""Tests for the export: an org's world whole as JSON Lines, and nothing of another org or world."""

import json

import pytest

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals import dataset
from pinecall.evals.dataset import Promoted
from pinecall.log import drift
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import canary, consents, export
from pinecall.tenancy.canary import Canary, CanarySet
from pinecall.tenancy.consents import Given
from pinecall.tenancy.prompts import Prompts
from pinecall.wire.rest.evals import Expect
from pinecall.wire.scores import CallScore
from tests.conftest import outlasting_a_lock, postgres
from tests.log.conftest import ACall, judgment, logged_call
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


async def test_the_drift_the_seal_counted_comes_out_as_numbers_by_day(
    pool: Pool, store: Store
) -> None:
    org = await an_org(pool)
    turn: JsonObject = {
        "speech_id": "a",
        "text": "claro",
        "interrupted": False,
        "metrics": {"llm_node_ttft": 0.5},
    }
    call = await logged_call(
        store, org.id, ACall(judges=(judgment("consent", "held"),), ended=False)
    )
    await store.append(call, "dental-sur", "turn.agent", turn, ephemeral=False)
    score: JsonObject = {"judges": [judgment("consent", "held")], "judge_calls": 0}
    await drift.fold(pool, call, await store.whole(call), CallScore.model_validate(score))

    lines = await exported(pool, org.id)

    stage = next(line for line in lines if line["kind"] == "stage_day")
    verdict = next(line for line in lines if line["kind"] == "judge_day")
    assert (stage["stage"], stage["turns"], stage["day"]) == ("llm", 1, "1970-01-01")
    assert (verdict["judge"], verdict["held"], verdict["broken"]) == ("consent", 1, 0)
    assert call not in json.dumps(lines[-2:]), "a day's numbers name no call"


async def test_the_orgs_cases_and_prompts_come_out_in_each_worlds_export(
    pool: Pool, store: Store
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    golden = dataset.golden_of(await store.whole(call), "hola", Expect())
    await dataset.promoted(pool, golden, "dental-sur", Promoted(org.id, "production", "hola", "m"))
    await Prompts().keep(pool, org.id, "Sos la recepción.")
    for world in ("production", "sandbox"):
        lines = await exported(pool, org.id, world)
        prompt = next(line for line in lines if line["kind"] == "prompt")
        assert prompt["text"] == "Sos la recepción."
        case = next(line for line in lines if line["kind"] == "eval_case")
        assert (case["name"], case["source_call"], case["source_env"]) == (
            "hola",
            call,
            "production",
        )


A_SETTING = """
INSERT INTO agent_config (org, env, holder, agent, version, config, author)
VALUES (%(org)s, 'production', '', 'agenda', 1, '{"slug": "agenda"}', 'm_ana')
"""

A_WORD = """
INSERT INTO lexicon (org, env, holder, agent, version, said, heard, author)
VALUES (%(org)s, 'production', '', 'agenda', 1, '{"ok": "vale"}', '[]', 'm_ana')
"""


async def test_the_settings_the_words_and_the_consents_come_out_too(pool: Pool) -> None:
    org = await an_org(pool)
    async with pool.connection() as connection:
        await connection.execute(A_SETTING, {"org": org.id})
        await connection.execute(A_WORD, {"org": org.id})
    await consents.give(pool, Scope(org.id), "+14155550142", Given("express", "the form", "m_ana"))
    tried = CanarySet(Canary(version=1, share=10), "m_ana", 1.0, "try it")
    await canary.put(pool, Scope(org.id), "agenda", tried)
    lines = await exported(pool, org.id)
    kinds = [line["kind"] for line in lines]
    assert kinds == ["export", "agent_config", "canary", "lexicon", "consent"]
    assert (lines[1]["version"], lines[3]["said"], lines[4]["number"]) == (
        1,
        {"ok": "vale"},
        "+14155550142",
    )
    assert (lines[2]["version"], lines[2]["share"], lines[2]["note"]) == (1, 10, "try it")


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


async def test_an_export_runs_past_the_pools_statement_timeout(
    pool: Pool, impatient_pool: Pool, store: Store, schema: str
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)

    async def lines() -> list[JsonObject]:
        return await exported(impatient_pool, org.id)

    exported_lines = await outlasting_a_lock(schema, "call_log", lines)
    assert [line["call"] for line in exported_lines if line["kind"] == "call"] == [call]
