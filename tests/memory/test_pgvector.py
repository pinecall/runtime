"""PgvectorMemory against the table 0008 made: both branches, the fusion, the history, writes."""

import pytest

from pinecall.log.store import Pool
from pinecall.memory import PgvectorMemory, Spoken
from pinecall.types import PRODUCTION, SANDBOX, MemoryPolicy
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
    facts = await memory.recall(org, PRODUCTION, None, contact, "turno de mañana")
    assert facts[0].text == "prefiere turnos por la mañana"
    assert facts[0].score == 1.0


async def test_a_fact_is_recalled_by_its_vector_when_its_words_say_nothing(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    """The dense branch alone: no word in common with the query, the same direction as it."""
    await a_row(pool, org, contact, "le gusta el café cortado", like="bebida caliente preferida")
    await a_row(pool, org, contact, "vive en Montevideo con su perro", like=NOWHERE)
    facts = await memory.recall(org, PRODUCTION, None, contact, "bebida caliente preferida")
    assert facts[0].text == "le gusta el café cortado"
    assert facts[0].score == 1.0


async def test_the_fact_both_branches_find_comes_first(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    await a_row(pool, org, contact, "prefiere turnos por la tarde", like=NOWHERE)
    await a_row(pool, org, contact, "vive en Montevideo", like="turnos por la mañana")
    await a_row(pool, org, contact, "prefiere turnos por la mañana")
    facts = await memory.recall(org, PRODUCTION, None, contact, "turnos por la mañana")
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
    recalled = await memory.recall(org, PRODUCTION, None, contact, "turnos")
    assert [fact.text for fact in recalled] == ["prefiere turnos por la tarde"]
    history = await memory.history(org, PRODUCTION, None, contact)
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
    facts = await memory.recall(org, PRODUCTION, None, contact, "turnos", as_of=LEARNED)
    assert [fact.text for fact in facts] == ["prefiere turnos por la mañana"]


async def test_forget_takes_every_row_of_the_contact_and_answers_how_many(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    for text in ("uno", "dos", "tres"):
        await a_row(pool, org, contact, text)
    await a_row(pool, org, "somebody-else", "cuatro")
    assert await memory.forget(org, PRODUCTION, None, contact) == 3
    assert await memory.forget(org, PRODUCTION, None, contact) == 0
    assert await memory.history(org, PRODUCTION, None, contact) == []
    assert len(await memory.history(org, PRODUCTION, None, "somebody-else")) == 1


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
        PRODUCTION,
        None,
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
    current = [
        fact
        for fact in await memory.history(org, PRODUCTION, None, contact)
        if fact.invalidated_at is None
    ]
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
        org,
        PRODUCTION,
        None,
        contact,
        [],
        channel="whatsapp",
        at=HUNG_UP,
        policy=THE_POLICY,
        llm=None,
        keys={},
    )
    assert [fact.text for fact in ops[0].facts] == ["prefiere que le hablen de usted"]
    assert [fact.text for fact in await memory.history(org, PRODUCTION, None, contact)] == [
        "prefiere que le hablen de usted"
    ]


async def test_a_tenant_that_named_nothing_to_remember_asks_no_model_and_writes_nothing(
    memory: PgvectorMemory, org: str, contact: str, models: ScriptedModels
) -> None:
    turns = [Spoken("user", "soy alérgico a la penicilina")]
    ops = await memory.remember(
        org,
        PRODUCTION,
        None,
        contact,
        turns,
        channel="web",
        at=HUNG_UP,
        policy=MemoryPolicy(),
        llm=None,
        keys={},
    )
    assert ops[0].facts == []
    assert models.built == []
    assert await memory.history(org, PRODUCTION, None, contact) == []


async def test_a_model_that_answers_garbage_writes_nothing_and_raises_nothing(
    memory: PgvectorMemory, org: str, contact: str, models: ScriptedModels
) -> None:
    models.answer = "Claro, el paciente prefiere la tarde."
    ops = await memory.remember(
        org,
        PRODUCTION,
        None,
        contact,
        [],
        channel="phone",
        at=HUNG_UP,
        policy=THE_POLICY,
        llm=None,
        keys={},
    )
    assert ops[0].facts == []
    assert await memory.history(org, PRODUCTION, None, contact) == []


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
    facts = await memory.recall(org, PRODUCTION, None, contact, "café cortado")
    assert [fact.text for fact in facts] == ["vive en Montevideo", "prefiere el café cortado"]


async def test_a_fact_written_now_carries_the_model_that_embedded_it(
    memory: PgvectorMemory, models: ScriptedModels, pool: Pool, org: str, contact: str
) -> None:
    models.answer = '[{"op": "add", "text": "prefiere la mañana", "category": "preference"}]'
    await memory.remember(
        org,
        PRODUCTION,
        None,
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
    assert await memory.forget(org, PRODUCTION, None, contact) == 2
    assert await memory.kept(org) == 1


# ── the write a golden makes ────────────────────────────────────────────────────


# What a memory golden needs and a call never asks for: the facts are GIVEN, not extracted, so the
# ranking it then measures is the real one — the same two index scans, the same fusion, the same
# embedder — over a contact nobody has. docs/retrieval/spec.md.
async def test_hold_writes_the_sentences_it_was_given_and_asks_no_model(
    memory: PgvectorMemory, org: str, contact: str, models: ScriptedModels
) -> None:
    said = ["prefiere turnos por la mañana", "vive en Montevideo con su perro"]
    await memory.hold(org, PRODUCTION, None, contact, said, at=HUNG_UP)
    held = await memory.history(org, PRODUCTION, None, contact)
    # A set, because the rows share one valid_from and the history's tie-break is the row id: two
    # facts written in the same breath have no order between them, and a golden asks for none.
    assert {fact.text for fact in held} == set(said)
    assert [fact.valid_from for fact in held] == [HUNG_UP, HUNG_UP]
    assert [fact.category for fact in held] == [None, None]
    assert models.built == [], "a golden pays for no model call, only for the embedding"


async def test_a_fact_a_golden_held_is_recalled_by_the_two_branches_a_turn_reads(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    """The point of writing them: what comes back is what a call would get, in that order."""
    await memory.hold(
        org,
        PRODUCTION,
        None,
        contact,
        ["prefiere turnos por la mañana", "vive en Montevideo con su perro", NOWHERE],
        at=HUNG_UP,
    )
    facts = await memory.recall(org, PRODUCTION, None, contact, "turno de mañana", k=2)
    assert [fact.text for fact in facts][0] == "prefiere turnos por la mañana"
    assert facts[0].score == 1.0
    assert len(facts) == 2, "k cuts, which is what makes a golden's recall@k a real question"


async def test_the_facts_a_golden_held_carry_this_embedder_and_go_with_one_forget(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    await memory.hold(
        org, PRODUCTION, None, contact, ["prefiere la mañana", "vive en Pocitos"], at=HUNG_UP
    )
    rows = await pool.fetch(
        "SELECT model FROM contact_memories WHERE org = $1 AND contact = $2", org, contact
    )
    assert [row["model"] for row in rows] == [HASH_MODEL, HASH_MODEL]
    assert await memory.forget(org, PRODUCTION, None, contact) == 2
    assert await memory.history(org, PRODUCTION, None, contact) == []


async def test_a_contacts_facts_are_one_worlds_and_a_test_call_never_reaches_the_real_ones(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    """The point of 0018: that contact under a laptop's key is another contact to memory."""
    await memory.hold(
        org, PRODUCTION, None, contact, ["prefiere que la llamen a la tarde"], at=HUNG_UP
    )
    await memory.hold(
        org, SANDBOX, None, contact, ["test: dice que su pedido no llegó"], at=HUNG_UP
    )
    deployed = await memory.recall(org, PRODUCTION, None, contact, "pedido")
    written = await memory.recall(org, SANDBOX, None, contact, "pedido")
    assert [fact.text for fact in deployed] == ["prefiere que la llamen a la tarde"]
    assert [fact.text for fact in written] == ["test: dice que su pedido no llegó"]
    # Forgetting the laptop's contact leaves the real one's facts standing; the quota saw both.
    assert await memory.kept(org) == 2
    assert await memory.forget(org, SANDBOX, None, contact) == 1
    assert len(await memory.history(org, PRODUCTION, None, contact)) == 1
    assert await memory.kept(org) == 1


async def test_an_agents_facts_are_the_current_ones_its_calls_taught_newest_first(
    memory: PgvectorMemory, pool: Pool, org: str, contact: str
) -> None:
    """Across contacts, by the call that taught each; a golden's fact belongs to no agent."""
    agent, call, other_call = f"agent-{org}", f"CA_{org}", f"CA_other_{org}"
    await pool.execute(
        "insert into call_log_head (log, agent, call, org) "
        "values ($1, $2, $1, $3), ($4, $5, $4, $3)",
        call,
        agent,
        org,
        other_call,
        "another-agent",
    )
    old = await a_row(pool, org, contact, "prefiere la tarde", source=call, learned=LEARNED)
    new = await a_row(pool, org, "+34611", "tiene un perro", source=call, learned=HUNG_UP)
    await a_row(pool, org, contact, "otro agente lo sabe", source=other_call)
    await a_row(pool, org, contact, "ya no vale", source=call, invalidated=HUNG_UP)
    await a_row(pool, org, contact, "un golden lo trajo")
    first = await memory.taught_by(org, PRODUCTION, None, agent, words=None, after=None, limit=1)
    assert [fact.id for fact in first.facts] == [new]
    rest = await memory.taught_by(
        org, PRODUCTION, None, agent, words=None, after=first.next, limit=5
    )
    assert ([fact.id for fact in rest.facts], rest.next) == ([old], None)
    found = await memory.taught_by(org, PRODUCTION, None, agent, words="PERRO", after=None, limit=5)
    assert [fact.id for fact in found.facts] == [new]
    assert await memory.invalidated(org, PRODUCTION, None, new, HUNG_UP) is True
    assert await memory.invalidated(org, PRODUCTION, None, new, HUNG_UP) is False
    assert await memory.invalidated(org, SANDBOX, None, old, HUNG_UP) is False, "another world"
    history = await memory.history(org, PRODUCTION, None, "+34611")
    assert history[0].invalidated_at == HUNG_UP, "the row stays, with when it stopped holding"
