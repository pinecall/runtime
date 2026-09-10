"""PgvectorMemory against the table 0008 made: both branches, the fusion, the history, writes."""

import pytest

from pinecall.log.store import Pool
from pinecall.memory import PgvectorMemory, Spoken
from pinecall.types import MemoryPolicy
from tests.memory.conftest import HUNG_UP, LEARNED, ScriptedModels, a_row
from tests.vectors import HASH_MODEL

pytestmark = pytest.mark.postgres

THE_POLICY = MemoryPolicy(remember=("preference", "health"), forget=("religion",))

# Words that share nothing with any query below: a vector hashed from them points nowhere near.
NOWHERE = "zzz qqq xxx"


async def test_a_fact_is_recalled_by_its_words_when_its_vector_says_nothing(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    """The BM25 branch alone: the words match after stemming, the vector was hashed elsewhere."""
    await a_row(pool, org, contact, "prefiere turnos por la mañana", like=NOWHERE)
    await a_row(pool, org, contact, "vive en Montevideo con su perro", like="perro casa")
    facts = await memory.recall(org, contact, "turno de mañana")
    assert facts[0].text == "prefiere turnos por la mañana"
    assert facts[0].score == 1.0


async def test_a_fact_is_recalled_by_its_vector_when_its_words_say_nothing(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    """The dense branch alone: no word in common with the query, the same direction as it."""
    await a_row(pool, org, contact, "le gusta el café cortado", like="bebida caliente preferida")
    await a_row(pool, org, contact, "vive en Montevideo con su perro", like=NOWHERE)
    facts = await memory.recall(org, contact, "bebida caliente preferida")
    assert facts[0].text == "le gusta el café cortado"
    assert facts[0].score == 1.0


async def test_the_fact_both_branches_find_comes_first(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    await a_row(pool, org, contact, "prefiere turnos por la tarde", like=NOWHERE)
    await a_row(pool, org, contact, "vive en Montevideo", like="turnos por la mañana")
    await a_row(pool, org, contact, "prefiere turnos por la mañana")
    facts = await memory.recall(org, contact, "turnos por la mañana")
    assert facts[0].text == "prefiere turnos por la mañana"
    assert facts[0].score == 1.0
    assert {fact.text for fact in facts[1:]} == {
        "prefiere turnos por la tarde",
        "vive en Montevideo",
    }
    assert all(fact.score < 1.0 for fact in facts[1:])


async def test_an_invalidated_fact_is_not_recalled_but_is_in_the_history(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    await a_row(pool, org, contact, "prefiere turnos por la mañana", invalidated=HUNG_UP)
    await a_row(pool, org, contact, "prefiere turnos por la tarde")
    recalled = await memory.recall(org, contact, "turnos")
    assert [fact.text for fact in recalled] == ["prefiere turnos por la tarde"]
    history = await memory.history(org, contact)
    assert [fact.text for fact in history] == [
        "prefiere turnos por la tarde",
        "prefiere turnos por la mañana",
    ]
    assert history[0].invalidated_at is None
    assert history[1].invalidated_at == HUNG_UP


async def test_a_recall_as_of_a_moment_reads_what_held_then(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    await a_row(pool, org, contact, "prefiere turnos por la mañana", invalidated=HUNG_UP)
    await a_row(pool, org, contact, "prefiere turnos por la tarde", learned=HUNG_UP)
    facts = await memory.recall(org, contact, "turnos", as_of=LEARNED)
    assert [fact.text for fact in facts] == ["prefiere turnos por la mañana"]


async def test_forget_takes_every_row_of_the_contact_and_answers_how_many(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    for text in ("uno", "dos", "tres"):
        await a_row(pool, org, contact, text)
    await a_row(pool, org, "somebody-else", "cuatro")
    assert await memory.forget(org, contact) == 3
    assert await memory.forget(org, contact) == 0
    assert await memory.history(org, contact) == []
    assert len(await memory.history(org, "somebody-else")) == 1


async def test_remember_adds_supersedes_and_invalidates_as_the_model_asked(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str, models: ScriptedModels
) -> None:
    morning = await a_row(pool, org, contact, "prefiere turnos por la mañana")
    pocitos = await a_row(pool, org, contact, "vive en Pocitos", category="address")
    models.answer = (
        '[{"op": "add", "text": "es alérgico a la penicilina", "category": "health"},'
        f' {{"op": "update", "of": "{morning}", "text": "prefiere turnos por la tarde",'
        ' "category": "preference"},'
        f' {{"op": "invalidate", "of": "{pocitos}"}}]'
    )
    turns = [Spoken("user", "me mudé, y ahora prefiero la tarde"), Spoken("agent", "anotado")]
    ops = await memory.remember(
        org,
        contact,
        turns,
        channel="phone",
        at=HUNG_UP,
        policy=THE_POLICY,
        llm=None,
        keys={},
        call="CA_1",
    )
    assert len(ops) == 1 and ops[0].op == "remember" and ops[0].contact == contact
    assert [(fact.text, fact.category, fact.source) for fact in ops[0].facts] == [
        ("es alérgico a la penicilina", "health", "CA_1"),
        ("prefiere turnos por la tarde", "preference", "CA_1"),
    ]
    assert ops[0].took_ms >= 0
    current = [fact for fact in await memory.history(org, contact) if fact.invalidated_at is None]
    assert {fact.text for fact in current} == {
        "es alérgico a la penicilina",
        "prefiere turnos por la tarde",
    }
    superseded = await pool.fetchrow(
        "SELECT supersedes::text AS of FROM contact_memories WHERE text = $1 AND contact = $2",
        "prefiere turnos por la tarde",
        contact,
    )
    assert superseded is not None and superseded["of"] == morning
    ended = await pool.fetch(
        "SELECT invalidated_at FROM contact_memories WHERE id = ANY($1::uuid[])",
        [morning, pocitos],
    )
    assert [row["invalidated_at"] for row in ended] == [HUNG_UP, HUNG_UP]
    assert len(models.built) == 1
    assert "The call, on phone:" in (models.built[0].asked[0].history[0].text_content or "")


async def test_a_forget_category_never_reaches_the_table_and_a_fence_is_forgiven(
    memory: PgvectorMemory, org: str, contact: str, models: ScriptedModels
) -> None:
    models.answer = (
        '```json\n[{"op": "add", "text": "es católico", "category": "religion"},'
        ' {"op": "add", "text": "prefiere que le hablen de usted", "category": "preference"}]\n```'
    )
    ops = await memory.remember(
        org, contact, [], channel="whatsapp", at=HUNG_UP, policy=THE_POLICY, llm=None, keys={}
    )
    assert [fact.text for fact in ops[0].facts] == ["prefiere que le hablen de usted"]
    assert [fact.text for fact in await memory.history(org, contact)] == [
        "prefiere que le hablen de usted"
    ]


async def test_a_tenant_that_named_nothing_to_remember_asks_no_model_and_writes_nothing(
    memory: PgvectorMemory, org: str, contact: str, models: ScriptedModels
) -> None:
    turns = [Spoken("user", "soy alérgico a la penicilina")]
    ops = await memory.remember(
        org, contact, turns, channel="web", at=HUNG_UP, policy=MemoryPolicy(), llm=None, keys={}
    )
    assert ops[0].facts == []
    assert models.built == []
    assert await memory.history(org, contact) == []


async def test_a_model_that_answers_garbage_writes_nothing_and_raises_nothing(
    memory: PgvectorMemory, org: str, contact: str, models: ScriptedModels
) -> None:
    models.answer = "Claro, el paciente prefiere la tarde."
    ops = await memory.remember(
        org, contact, [], channel="phone", at=HUNG_UP, policy=THE_POLICY, llm=None, keys={}
    )
    assert ops[0].facts == []
    assert await memory.history(org, contact) == []


# ── whose vectors these are ─────────────────────────────────────────────────────


async def test_a_fact_of_another_model_is_out_of_the_dense_branch_and_still_found_by_words(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    """The box changed embedder. The old fact's vector is a number of the same width and nothing
    more, so it is no candidate by MEANING any more — both rows point at the query and only the
    current model's is ranked for it. BM25 reads the text, so the old fact is not lost: it wins
    the words branch, and being in one branch of two is what puts it second instead of first."""
    await a_row(
        pool,
        org,
        contact,
        "prefiere el café cortado",
        like="café cortado",
        model="another-embedder",
    )
    await a_row(pool, org, contact, "vive en Montevideo", like="café cortado")
    facts = await memory.recall(org, contact, "café cortado")
    assert [fact.text for fact in facts] == ["vive en Montevideo", "prefiere el café cortado"]


async def test_a_fact_written_now_carries_the_model_that_embedded_it(
    memory: PgvectorMemory, models: ScriptedModels, pool: Pool, org: str, contact: str
) -> None:
    models.answer = '[{"op": "add", "text": "prefiere la mañana", "category": "preference"}]'
    await memory.remember(
        org,
        contact,
        [Spoken(role="user", text="mejor de mañana")],
        channel="phone",
        at=HUNG_UP,
        policy=THE_POLICY,
        llm=None,
        keys={},
    )
    rows = await pool.fetch(
        "SELECT model FROM contact_memories WHERE org = $1 AND contact = $2", org, contact
    )
    assert [row["model"] for row in rows] == [HASH_MODEL]


async def test_the_facts_an_org_keeps_are_counted_across_its_contacts_and_history_is_not(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    """What the memory_facts quota is measured against: the rows that hold, never every row."""
    assert await memory.kept(org) == 0
    await a_row(pool, org, contact, "prefiere la mañana")
    await a_row(pool, org, "another-contact", "vive en Montevideo")
    assert await memory.kept(org) == 2
    await a_row(pool, org, contact, "prefería la tarde", invalidated=HUNG_UP)
    assert await memory.kept(org) == 2, "a superseded row is history and not a fact held"
    assert await memory.forget(org, contact) == 2
    assert await memory.kept(org) == 1
