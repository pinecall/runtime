"""Tests for the knowledge and memory doors, knocked on a real gateway."""

import dataclasses
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from livekit.agents import llm

from pinecall.domain.agent import Docs, MemoryPolicy, Tuning
from pinecall.domain.names import PRODUCTION, SANDBOX, JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.gateway._deps import NO_EMBEDDER
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.app import app
from pinecall.postgres.pool import Pool
from pinecall.providers.catalog import Embedding
from pinecall.retrieval import memory
from pinecall.retrieval.embed import Embedder, halfvec
from pinecall.retrieval.knowledge import HEADING_SEPARATOR
from pinecall.tenancy import admission, scopes
from pinecall.tenancy.scopes import Written
from tests.conftest import AGENT, Knocking, issued, postgres, received_until, sent
from tests.fakes.acme import AcmeLLM
from tests.fakes.embeddings import Embeddings, Meanings

# ── knowledge ──

KNOWLEDGE = "/v1/knowledge"
EMBEDDING = Embedding(
    vendor="acme", url="https://embeddings.test/v1", model="embed-1", shape="embeddings"
)
TARIFAS = "# Tarifas\n\nLa revisión cuesta cuarenta y cinco euros."
A_PUSH = {"files": [{"path": "tarifas.md", "text": TARIFAS}]}
HORARIOS = "# Horarios\n\nDe nueve a veinte."
BY_THE_CONSOLE = Written(author="console")


@pytest.fixture
def meanings() -> Meanings:
    """The box's embeddings vendor."""
    return Meanings(words=("revisión", "horarios"))


@pytest.fixture
async def wired(wired: Gateway, meanings: Meanings) -> AsyncIterator[Gateway]:
    """The gateway of the suite, embedding with the fake vendor on its own client."""
    async with httpx.AsyncClient(transport=meanings.transport()) as http:
        yield dataclasses.replace(wired, embedder=Embedder(EMBEDDING, "a key of the box", http))


async def limited(knocking: Knocking, chunks: int | None) -> None:
    """The org's production quotas, replaced whole as the operator's door does."""
    await admission.set_quotas(
        knocking.gateway.connections.pool,
        knocking.org.id,
        PRODUCTION,
        Quotas(knowledge_chunks=chunks),
    )


@postgres
async def test_a_push_then_the_list_then_a_drop(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as cli:
        pushed = await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        assert pushed.status_code == 200
        answer = pushed.json()
        assert (answer["base"], answer["chunks"]) == ("clinica", 1)
        assert answer["took_ms"] >= 0
        [base] = (await cli.get(KNOWLEDGE)).json()["bases"]
        assert (base["base"], base["chunks"], base["model"]) == ("clinica", 1, EMBEDDING.model)
        assert isinstance(base["pushed_at"], float)
        assert (await cli.delete(f"{KNOWLEDGE}/clinica")).status_code == 204
        assert (await cli.get(KNOWLEDGE)).json() == {"bases": []}


@postgres
async def test_the_files_of_a_base_are_listed_read_put_and_taken_out_one_at_a_time(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        await console.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        listed = (await console.get(f"{KNOWLEDGE}/clinica")).json()
        assert (listed["base"], listed["kept"]) == ("clinica", True)
        [row] = listed["files"]
        assert (row["path"], row["chars"], row["chunks"]) == ("tarifas.md", len(TARIFAS), 1)

        put = await console.put(
            f"{KNOWLEDGE}/clinica/files/faq/horarios.md", json={"text": HORARIOS}
        )
        assert put.status_code == 200
        assert (put.json()["path"], put.json()["chunks"]) == ("faq/horarios.md", 1)
        read = (await console.get(f"{KNOWLEDGE}/clinica/files/faq/horarios.md")).json()
        assert (read["path"], read["text"], read["chunks"]) == ("faq/horarios.md", HORARIOS, 1)

        cheaper = "# Tarifas\n\nLa revisión cuesta cuarenta."
        replaced = await console.put(
            f"{KNOWLEDGE}/clinica/files/tarifas.md", json={"text": cheaper}
        )
        assert replaced.status_code == 200
        assert (await console.get(f"{KNOWLEDGE}/clinica/files/tarifas.md")).json()[
            "text"
        ] == cheaper

        assert (await console.delete(f"{KNOWLEDGE}/clinica/files/tarifas.md")).status_code == 204
        files = (await console.get(f"{KNOWLEDGE}/clinica")).json()["files"]
        assert [item["path"] for item in files] == ["faq/horarios.md"]


@postgres
async def test_a_file_put_into_no_base_begins_one(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        put = await console.put(
            f"{KNOWLEDGE}/nueva/files/inicio.md", json={"text": "# Inicio\n\nHola."}
        )
        assert put.status_code == 200
        [base] = (await console.get(KNOWLEDGE)).json()["bases"]
    assert (base["base"], base["chunks"]) == ("nueva", 1)


@postgres
async def test_a_file_nobody_put_is_a_refusal_that_names_it(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as console:
        await console.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        read = await console.get(f"{KNOWLEDGE}/clinica/files/nadie.md")
        dropped = await console.delete(f"{KNOWLEDGE}/clinica/files/nadie.md")
        listed = await console.get(f"{KNOWLEDGE}/nadie")
    assert read.status_code == 404
    assert read.json()["detail"] == "no file nadie.md in the base clinica"
    assert dropped.status_code == 404
    assert listed.status_code == 404


@postgres
async def test_dropping_a_base_nobody_pushed_is_a_refusal_that_names_it(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as cli:
        refused = await cli.delete(f"{KNOWLEDGE}/nadie")
    assert refused.status_code == 404
    assert (
        refused.json()["detail"]
        == "no knowledge base named nadie: nothing was pushed under that name"
    )


@postgres
async def test_the_doors_take_the_orgs_key_and_nothing_else(knocking: Knocking) -> None:
    async with knocking.http("pc_live_nobody") as stranger:
        refused = await stranger.get(KNOWLEDGE)
    assert refused.status_code == 401


@postgres
async def test_a_box_that_embeds_nothing_refuses_a_push_saying_what_the_operator_sets(
    knocking: Knocking,
) -> None:
    app.state.gateway = dataclasses.replace(knocking.gateway, embedder=None)
    async with knocking.http(knocking.app["production"]) as cli:
        refused = await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        listed = await cli.get(KNOWLEDGE)
    assert refused.status_code == 503
    assert refused.json()["detail"] == NO_EMBEDDER
    assert listed.status_code == 200


@postgres
async def test_a_sandbox_key_pushes_to_the_sandbox_and_production_never_sees_it(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as sandbox:
        await sandbox.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
    async with knocking.http(knocking.app["production"]) as production:
        assert (await production.get(KNOWLEDGE)).json() == {"bases": []}


@postgres
async def test_the_agents_whose_settings_attach_a_base_are_listed_beside_it(
    knocking: Knocking,
) -> None:
    pool, org = knocking.gateway.connections.pool, knocking.org.id
    reading = Tuning(bases=(Docs(base="clinica"), Docs(base="precios", mode="tool")))
    await scopes.put_tuning(pool, Scope(org, PRODUCTION), "recepcion", reading, BY_THE_CONSOLE)
    await scopes.put_tuning(
        pool,
        Scope(org, PRODUCTION),
        "ventas",
        Tuning(bases=(Docs(base="clinica"),)),
        BY_THE_CONSOLE,
    )
    async with knocking.http(knocking.app["production"]) as console:
        uses = (await console.get(f"{KNOWLEDGE}/attached")).json()
    assert uses == {
        "bases": [
            {"base": "clinica", "agents": ["recepcion", "ventas"]},
            {"base": "precios", "agents": ["recepcion"]},
        ]
    }


# ── what the quota allows ──


@postgres
async def test_a_push_that_fits_lands_and_one_chunk_more_is_refused_with_both_numbers(
    knocking: Knocking,
) -> None:
    await limited(knocking, 1)
    async with knocking.http(knocking.app["production"]) as cli:
        assert (await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)).status_code == 200
        refused = await cli.put(f"{KNOWLEDGE}/tarifas", json=A_PUSH)
        listed = (await cli.get(KNOWLEDGE)).json()["bases"]
    assert refused.status_code == 429
    assert (
        refused.json()["detail"] == "the org has used 2 of its 1 knowledge chunks in the production"
    )
    assert [item["base"] for item in listed] == ["clinica"]


@postgres
async def test_pushing_a_base_again_frees_what_it_held_so_a_replacement_is_not_a_second_copy(
    knocking: Knocking,
) -> None:
    await limited(knocking, 1)
    async with knocking.http(knocking.app["production"]) as cli:
        assert (await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)).status_code == 200
        again = await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        file_again = await cli.put(f"{KNOWLEDGE}/clinica/files/tarifas.md", json={"text": TARIFAS})
    assert again.status_code == 200
    assert file_again.status_code == 200


@postgres
async def test_a_plan_that_keeps_no_chunks_refuses_the_first_push_in_a_sentence(
    knocking: Knocking,
) -> None:
    await limited(knocking, 0)
    async with knocking.http(knocking.app["production"]) as cli:
        refused = await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        listed = (await cli.get(KNOWLEDGE)).json()
    assert refused.status_code == 429
    assert (
        refused.json()["detail"] == "the org has used 1 of its 0 knowledge chunks in the production"
    )
    assert listed == {"bases": []}


@postgres
async def test_an_org_nobody_limited_pushes_whatever_it_likes(knocking: Knocking) -> None:
    await limited(knocking, None)
    async with knocking.http(knocking.app["production"]) as cli:
        for base in ("clinica", "tarifas", "horarios"):
            assert (await cli.put(f"{KNOWLEDGE}/{base}", json=A_PUSH)).status_code == 200


@postgres
async def test_a_drop_and_the_listing_are_never_refused_by_a_quota(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as cli:
        await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        await limited(knocking, 0)
        assert (await cli.get(KNOWLEDGE)).status_code == 200
        assert (await cli.delete(f"{KNOWLEDGE}/clinica")).status_code == 204


@postgres
async def test_a_sandbox_push_is_counted_against_the_sandboxs_quota_alone(
    knocking: Knocking,
) -> None:
    await limited(knocking, 1)
    async with knocking.http(knocking.app["production"]) as production:
        assert (await production.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)).status_code == 200
    await admission.set_quotas(
        knocking.gateway.connections.pool, knocking.org.id, SANDBOX, Quotas(knowledge_chunks=1)
    )
    async with knocking.http(knocking.app["sandbox"]) as sandbox:
        assert (await sandbox.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)).status_code == 200


# ── the golden ──

A_GOLDEN = {"questions": [{"asks": "la revisión", "expects": "tarifas.md"}]}


@postgres
async def test_a_base_that_answers_every_question_first_scores_one_on_both(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as cli:
        await cli.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        answer = (await cli.post(f"{KNOWLEDGE}/clinica/eval", json=A_GOLDEN)).json()
    assert (answer["questions"], answer["recall_at_k"], answer["ndcg_at_10"]) == (1, 1.0, 1.0)
    assert (answer["k"], answer["model"], answer["misses"]) == (8, EMBEDDING.model, [])


@postgres
async def test_a_question_the_base_misses_comes_back_with_what_it_found_instead(
    knocking: Knocking,
) -> None:
    horarios = {
        "files": [{"path": "horarios.md", "text": "# Horario de consulta\n\nDe nueve a veinte."}]
    }
    async with knocking.http(knocking.app["production"]) as cli:
        await cli.put(f"{KNOWLEDGE}/clinica", json=horarios)
        answer = (await cli.post(f"{KNOWLEDGE}/clinica/eval", json=A_GOLDEN)).json()
    assert answer["recall_at_k"] == 0.0
    [missed] = answer["misses"]
    assert missed["expects"] == "tarifas.md"
    assert missed["found"] == [HEADING_SEPARATOR.join(("horarios.md", "Horario de consulta"))]


@postgres
async def test_a_golden_against_a_base_nobody_pushed_is_the_refusal_a_drop_answers(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as cli:
        refused = await cli.post(f"{KNOWLEDGE}/nadie/eval", json=A_GOLDEN)
    assert refused.status_code == 404
    assert (
        refused.json()["detail"]
        == "no knowledge base named nadie: nothing was pushed under that name"
    )


# ── memory ──

MODEL = "embed-flat-1"
FLAT = Embedding(vendor="acme-embed", url="https://embed.test/v1", model=MODEL, shape="embeddings")
CONTACT = "+34600000001"
A_CONTACT = f"/v1/contacts/{CONTACT}/memory"
EVAL = "/v1/contacts/memory/eval"
TAUGHT = f"/v1/agents/{AGENT}/memory"
EXTRACTION = f"/v1/agents/{AGENT}/memory/extraction"
LEARNED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

MORNINGS = "Prefiere mañanas"
PENICILLIN = "Alérgica a la penicilina"
A_QUESTION = {"holds": [MORNINGS, PENICILLIN], "asks": "¿prefiere mañanas?", "expects": [MORNINGS]}

A_FACT = """
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, model, source_call)
VALUES (%(org)s, 'production', '', %(contact)s, %(text)s, 'preference',
    %(embedding)s::halfvec, %(learned)s, %(model)s, %(call)s)
RETURNING id::text AS id
"""
A_CALL_OF = """
INSERT INTO call_log_head (log, agent, call, org) VALUES (%(call)s, %(agent)s, %(call)s, %(org)s)
"""


@pytest.fixture
async def vendor(knocking: Knocking) -> AsyncIterator[Embeddings]:
    """The box's embedder on a fake vendor, wired into the gateway the test knocks on."""
    answering = Embeddings()
    async with httpx.AsyncClient(transport=answering.transport()) as http:
        embeds = Embedder(FLAT, "a-key", http)
        app.state.gateway = dataclasses.replace(knocking.gateway, embedder=embeds)
        yield answering


async def a_fact(
    pool: Pool, org: str, text: str, *, call: str | None = None, learned: datetime = LEARNED
) -> str:
    """A fact of the contact in the org's own production corner; its id."""
    params = {
        "org": org,
        "contact": CONTACT,
        "text": text,
        "embedding": halfvec([1.0] + [0.0] * 1023),
        "learned": learned,
        "model": MODEL,
        "call": call,
    }
    async with pool.connection() as connection:
        found = await (await connection.execute(A_FACT, params)).fetchone()
    assert found is not None
    return str(found["id"])


async def taught_by(pool: Pool, org: str, agent: str) -> str:
    """A call of the agent, as its log's head names it; its id."""
    call = f"CA_{uuid4().hex[:12]}"
    async with pool.connection() as connection:
        await connection.execute(A_CALL_OF, {"call": call, "agent": agent, "org": org})
    return call


# ── a contact's memory ──


@postgres
async def test_the_history_then_forgetting_it(knocking: Knocking) -> None:
    pool, org = knocking.gateway.connections.pool, knocking.org.id
    await a_fact(pool, org, "prefiere turnos por la mañana", learned=LATER)
    await a_fact(pool, org, "vive en Montevideo")
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        read = await http.get(A_CONTACT)
        assert read.status_code == 200
        facts = read.json()["facts"]
        assert [fact["text"] for fact in facts] == [
            "prefiere turnos por la mañana",
            "vive en Montevideo",
        ]
        assert facts[0]["valid_from"] == LATER.timestamp()
        assert facts[0]["invalidated_at"] is None
        assert facts[0]["category"] == "preference"
        forgotten = await http.delete(A_CONTACT)
        assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 2})
        assert (await http.get(A_CONTACT)).json() == {"facts": []}


@postgres
async def test_forgetting_a_stranger_answers_zero_and_never_404(knocking: Knocking) -> None:
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        forgotten = await http.delete("/v1/contacts/nobody/memory")
    assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 0})


@postgres
async def test_reading_and_forgetting_work_on_a_plan_that_keeps_no_memory_at_all(
    knocking: Knocking,
) -> None:
    pool, org = knocking.gateway.connections.pool, knocking.org.id
    await admission.set_quotas(pool, org, PRODUCTION, Quotas(memory_facts=0, knowledge_chunks=0))
    await a_fact(pool, org, "prefiere turnos por la mañana")
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        read = await http.get(A_CONTACT)
        forgotten = await http.delete(A_CONTACT)
    assert (read.status_code, len(read.json()["facts"])) == (200, 1)
    assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 1})


# ── the golden ──


@postgres
async def test_a_golden_writes_its_own_facts_asks_them_and_leaves_no_contact_behind(
    knocking: Knocking, vendor: Embeddings
) -> None:
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        answer = (await http.post(EVAL, json={"questions": [A_QUESTION], "k": 4})).json()
    assert (answer["questions"], answer["k"]) == (1, 4)
    assert (answer["recall_at_k"], answer["ndcg_at_10"]) == (1.0, 1.0)
    assert answer["misses"] == []
    assert answer["model"] == MODEL
    assert answer["took_ms"] >= 0
    assert vendor.inputs() == [[MORNINGS, PENICILLIN], ["¿prefiere mañanas?"]]
    assert await memory.kept(knocking.gateway.connections.pool, knocking.org.id, PRODUCTION) == 0


@postgres
async def test_a_question_it_misses_names_what_was_missing_and_what_came_back(
    knocking: Knocking, vendor: Embeddings
) -> None:
    del vendor
    missed = {**A_QUESTION, "expects": ["Vive en Pocitos"]}
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        answer = (await http.post(EVAL, json={"questions": [missed]})).json()
    assert (answer["recall_at_k"], answer["ndcg_at_10"]) == (0.0, 0.0)
    (miss,) = answer["misses"]
    assert miss["asks"] == "¿prefiere mañanas?"
    assert miss["missing"] == ["Vive en Pocitos"]
    assert set(miss["found"]) == {MORNINGS, PENICILLIN}


@postgres
async def test_each_question_is_asked_of_its_own_facts_and_nobody_elses(
    knocking: Knocking, vendor: Embeddings
) -> None:
    del vendor
    other = {"holds": ["Vive en Pocitos"], "asks": "martes", "expects": ["Vive en Pocitos"]}
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        answer = (await http.post(EVAL, json={"questions": [A_QUESTION, other], "k": 1})).json()
    assert (answer["questions"], answer["recall_at_k"], answer["misses"]) == (2, 1.0, [])


@postgres
async def test_a_golden_asked_with_no_k_is_asked_with_the_facts_a_turn_gets(
    knocking: Knocking, vendor: Embeddings
) -> None:
    del vendor
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        answer = (await http.post(EVAL, json={"questions": [A_QUESTION]})).json()
    assert answer["k"] == memory.DEFAULT_FACTS_PER_TURN


@postgres
async def test_the_scratch_contact_is_forgotten_even_when_a_question_fails(
    knocking: Knocking, vendor: Embeddings
) -> None:
    response = httpx.Response(
        200, json={"data": [{"index": at, "embedding": [0.5] * 1024} for at in range(2)]}
    )
    refused = httpx.Response(400, json={"error": {"message": "the key was revoked"}})
    vendor.script.extend([response, refused])
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        failed = await http.post(EVAL, json={"questions": [A_QUESTION]})
    assert failed.status_code == 503
    assert "the key was revoked" in failed.json()["detail"]
    assert await memory.kept(knocking.gateway.connections.pool, knocking.org.id, PRODUCTION) == 0


@postgres
async def test_a_golden_on_a_box_that_embeds_nothing_is_refused_in_a_sentence(
    knocking: Knocking,
) -> None:
    app.state.gateway = dataclasses.replace(knocking.gateway, embedder=None)
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        refused = await http.post(EVAL, json={"questions": [A_QUESTION]})
    assert (refused.status_code, refused.json()["detail"]) == (503, NO_EMBEDDER)


# ── across contacts ──


@postgres
async def test_the_agents_facts_are_listed_a_page_at_a_time(knocking: Knocking) -> None:
    pool, org = knocking.gateway.connections.pool, knocking.org.id
    call = await taught_by(pool, org, AGENT)
    await a_fact(pool, org, "prefiere la mañana", call=call, learned=LATER)
    dog = await a_fact(pool, org, "tiene un perro", call=call)
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        first = (await http.get(TAUGHT, params={"limit": 1})).json()
        assert [fact["text"] for fact in first["facts"]] == ["prefiere la mañana"]
        assert set(first["facts"][0]) == {"id", "contact", "text", "category", "written_at"}
        rest = (await http.get(TAUGHT, params={"limit": 1, "after": first["next"]})).json()
        assert ([fact["id"] for fact in rest["facts"]], rest["next"]) == ([dog], None)
        found = (await http.get(TAUGHT, params={"q": "perro"})).json()
        assert [fact["id"] for fact in found["facts"]] == [dog]


@postgres
async def test_every_agents_facts_are_listed_each_with_the_agent_that_taught_it(
    knocking: Knocking,
) -> None:
    pool, org = knocking.gateway.connections.pool, knocking.org.id
    await a_fact(pool, org, "prefiere la mañana", call=await taught_by(pool, org, AGENT))
    await a_fact(pool, org, "tiene un perro", call=await taught_by(pool, org, "otro"))
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        first = (await http.get("/v1/memory", params={"limit": 1})).json()
        rest = (await http.get("/v1/memory", params={"after": first["next"]})).json()
    listed = [(fact["text"], fact["agent"]) for fact in first["facts"] + rest["facts"]]
    assert sorted(listed) == [("prefiere la mañana", AGENT), ("tiene un perro", "otro")]
    assert rest["next"] is None


@postgres
async def test_a_cursor_that_is_not_one_is_refused_and_never_read_as_the_first_page(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        refused = await http.get("/v1/memory", params={"after": "page-2"})
    assert refused.status_code == 400
    assert "is not a cursor" in refused.json()["detail"]


@postgres
async def test_one_fact_is_forgotten_and_a_second_time_there_is_nothing_to_forget(
    knocking: Knocking,
) -> None:
    pool, org = knocking.gateway.connections.pool, knocking.org.id
    call = await taught_by(pool, org, AGENT)
    item = await a_fact(pool, org, "prefiere la mañana", call=call)
    two = await a_fact(pool, org, "tiene un perro", call=call)
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        forgotten = await http.delete(f"/v1/memory/facts/{item}")
        assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 1})
        again = await http.delete(f"/v1/memory/facts/{item}")
        assert (again.status_code, again.json()["detail"]) == (
            404,
            f"no current fact {item} in this key's org and world",
        )
        assert [fact["id"] for fact in (await http.get(TAUGHT)).json()["facts"]] == [two]
        assert (await http.delete("/v1/memory/facts/not-an-id")).status_code == 422


@postgres
async def test_a_key_without_memory_is_refused(knocking: Knocking) -> None:
    reader = await issued(
        knocking.gateway.connections.pool, knocking.org.id, PRODUCTION, frozenset({"calls"})
    )
    async with knocking.http(reader) as http:
        refused = await http.get(TAUGHT)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "this key does not open memory: it opens calls"


# ── extraction goldens ──

A_CARD = "4242 4242 4242 4242"
A_CALL = [
    ["caller", "Soy Marta, alérgica a la penicilina"],
    ["agent", "Anotado. ¿Le va bien el martes?"],
    ["caller", f"Mejor por la mañana, y le paso la Visa {A_CARD}"],
]
CLARAS_MEMORY = MemoryPolicy(
    remember=("cómo prefiere que le llamen", "alergias", "su médico habitual"), forget=("pagos",)
)
A_TOOL: JsonObject = {
    "name": "book",
    "description": "Books a slot",
    "parameters": {"type": "object"},
}
WHAT_THE_MODEL_ANSWERED = (
    '[{"op": "add", "text": "Es alérgica a la penicilina", "category": "alergias"},'
    ' {"op": "add", "text": "Prefiere por la mañana", "category": "cómo prefiere que le llamen"},'
    f' {{"op": "add", "text": "Paga con la Visa {A_CARD}", "category": "pagos"}}]'
)


@pytest.fixture
def models(knocking: Knocking, monkeypatch: pytest.MonkeyPatch) -> list[AcmeLLM]:
    """Every scripted model the gateway builds, in order; each answers WHAT_THE_MODEL_ANSWERED."""
    del knocking
    built: list[AcmeLLM] = []

    class Kept(AcmeLLM):
        def __init__(
            self,
            *,
            api_key: str,
            model: str = "acme-1",
            temperature: float = 1.0,
            replies: list[list[str | dict[str, object]]] | None = None,
        ) -> None:
            del replies
            answered: list[list[str | dict[str, object]]] = [[WHAT_THE_MODEL_ANSWERED]]
            super().__init__(
                api_key=api_key, model=model, temperature=temperature, replies=answered
            )
            built.append(self)

    monkeypatch.setattr(sys.modules["livekit.plugins.acme"], "LLM", Kept)
    return built


async def declared(knocking: Knocking, policy: MemoryPolicy | None = CLARAS_MEMORY) -> None:
    """The agent held in production by an app with one tool, its settings keeping the policy."""
    scope = Scope(knocking.org.id)
    await scopes.put_tuning(
        knocking.gateway.connections.pool,
        scope,
        AGENT,
        Tuning(memory=policy),
        scopes.Written(author="k_1"),
    )
    socket = await knocking.socket("/v1/apps", knocking.app[PRODUCTION])
    await sent(socket, "agent.register", {"routes": [], "takes_unclaimed": True})
    await received_until(socket, "agent.registered")
    await sent(socket, "agent.configure", {"config": {"tools": [A_TOOL]}})
    await received_until(socket, "agent.configured")


@postgres
async def test_a_case_is_one_hang_up_and_the_answer_says_what_memory_would_have_kept(
    knocking: Knocking, models: list[AcmeLLM]
) -> None:
    await declared(knocking)
    case = {
        "name": "anota la alergia y nunca la tarjeta",
        "said": A_CALL,
        "expect": {"writes": ["alergias"], "never": ["pagos"], "never_says": [A_CARD]},
    }
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        answered = await http.post(EXTRACTION, json={"cases": [case]})
    assert answered.status_code == 200
    body = answered.json()
    assert (body["agent"], body["model"], body["cases"], body["held"]) == (
        AGENT,
        "acme/acme-1",
        1,
        1,
    )
    (result,) = body["results"]
    assert result["held"] is True
    assert result["wrote"] == [
        "add · alergias · Es alérgica a la penicilina",
        "add · cómo prefiere que le llamen · Prefiere por la mañana",
    ]
    assert result["refused"] == [f"add · pagos · Paga con la Visa {A_CARD}"]
    (model,) = models
    (request,) = model.requests
    shown = [str(item.text_content) for item in request.items if isinstance(item, llm.ChatMessage)]
    assert "user: Soy Marta, alérgica a la penicilina" in shown[1]


@postgres
async def test_a_case_that_did_not_hold_is_named_and_the_count_says_so(
    knocking: Knocking, models: list[AcmeLLM]
) -> None:
    await declared(knocking)
    case = {
        "name": "recuerda al médico",
        "said": A_CALL,
        "expect": {"writes": ["su médico habitual"]},
    }
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        body = (await http.post(EXTRACTION, json={"cases": [case, case]})).json()
    assert (body["cases"], body["held"]) == (2, 0)
    assert [[item["check"] for item in result["broke"]] for result in body["results"]] == [
        ["writes"],
        ["writes"],
    ]
    assert len(models) == 2


@postgres
async def test_a_golden_that_names_a_category_the_agent_never_declared_is_refused(
    knocking: Knocking, models: list[AcmeLLM]
) -> None:
    await declared(knocking)
    case = {"name": "seguros", "said": A_CALL, "expect": {"writes": ["seguros"]}}
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        refused = await http.post(EXTRACTION, json={"cases": [case]})
    assert refused.status_code == 400
    assert "which this agent's memory policy does not keep" in refused.json()["detail"]
    assert models == []


@postgres
async def test_an_agent_that_keeps_nothing_has_nothing_to_hold_to_a_golden(
    knocking: Knocking,
) -> None:
    await declared(knocking, policy=None)
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        refused = await http.post(EXTRACTION, json={"cases": []})
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        f"agent {AGENT} declares no extraction.remember: there is nothing to extract",
    )


@postgres
async def test_an_agent_no_app_is_holding_is_the_registrys_own_refusal(knocking: Knocking) -> None:
    async with knocking.http(knocking.app[PRODUCTION]) as http:
        refused = await http.post(EXTRACTION, json={"cases": []})
    assert (refused.status_code, refused.json()["detail"]) == (
        404,
        f"no app is holding agent {AGENT}",
    )
