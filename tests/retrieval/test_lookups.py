"""Tests for a call's lookups: what recall and search answer the model, and what the log keeps."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import httpx
import pytest

from pinecall.domain.agent import AgentConfig, Docs, KnowledgeFile, MemoryPolicy, ToolSpec
from pinecall.domain.call import CallContext, Contact, Route
from pinecall.domain.names import JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.log.logs import Log
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.providers.catalog import Embedding
from pinecall.retrieval import knowledge
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.knowledge import HEADING_SEPARATOR, Push, cut
from pinecall.retrieval.lookups import OnTheCall, heard_in, lookup
from pinecall.wire.frames import Entry
from pinecall.wire.rest.calls import LookupRequest
from tests.conftest import postgres
from tests.fakes.embeddings import Embeddings, Meanings
from tests.retrieval.conftest import CONTACT, HUNG_UP, ARow, written

AGENT = "agenda"

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

PUSHED_AT = 1_000.0

MEANING = Embedding(
    vendor="acme-embed", url="https://embed.test/v1", model="embed-flat-1", shape="embeddings"
)

TARIFAS = KnowledgeFile(
    "tarifas.md", "# Tarifas\n\n## Revisión\n\nLa revisión cuesta cuarenta euros.\n"
)

HORARIOS = KnowledgeFile("horarios.md", "# Horarios\n\nAbrimos de nueve a veinte.\n")

KEEPS = MemoryPolicy(remember=("preference",))

READS = (Docs(base="clinica", k=2),)


@pytest.fixture
def meanings() -> Meanings:
    """An embedder whose vectors tell a revisión from a horario."""
    return Meanings(words=("revisión", "horarios", "alergia"))


@pytest.fixture
async def embedder(meanings: Meanings) -> AsyncIterator[Embedder]:
    """The box's embedder on the meaning vendor."""
    async with httpx.AsyncClient(transport=meanings.transport()) as http:
        yield Embedder(MEANING, "a-key", http)


def a_call(
    log: Log,
    scope: Scope,
    config: AgentConfig,
    *,
    channel: str = "phone",
    contact: Contact | None = None,
) -> OnTheCall:
    """A call of the agent in the scope, on the phone unless said, with its log."""
    route = Route(
        org=scope.org,
        agent=AGENT,
        channel="phone" if channel == "phone" else "web",
        number="+59829001199" if channel == "phone" else None,
        env=scope.env,
    )
    context = CallContext(
        call=log.name,
        channel=route.channel,
        direction="inbound",
        caller=CONTACT if channel == "phone" else "web_visitor",
        route=route,
        today=date(2026, 9, 28),
        contact=contact,
    )
    return OnTheCall(scope=scope, context=context, config=config, log=log, now=NOW)


def a_lookup(tool: str, query: str, **more: object) -> LookupRequest:
    """What the worker or the app asks."""
    return LookupRequest.model_validate({"tool": tool, "input": {"query": query, **more}})


async def entries_of(store: Store, log: Log) -> list[Entry]:
    return await store.whole(log.name)


# ── recall ──


@postgres
async def test_recall_on_a_phone_call_answers_facts_with_their_source_and_since(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await written(pool, scope, ARow(text="Es alérgica a la penicilina", source="CA_first"))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS))
    answered = await lookup(pool, embedder, on, a_lookup("recall", "alergia"), quotas=Quotas())
    assert answered == {
        "facts": [
            {"text": "Es alérgica a la penicilina", "source": "CA_first", "since": "2026-09-01"}
        ]
    }
    (entry,) = await entries_of(store, log)
    assert entry.type == "memory.ops"
    ops = entry.data["ops"]
    assert isinstance(ops, list)
    op = ops[0]
    assert isinstance(op, dict)
    assert (op["op"], op["contact"], op["query"]) == ("recall", CONTACT, "alergia")


@postgres
async def test_a_resolved_contact_id_outranks_the_number_it_called_from(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await written(pool, scope, ARow(text="Prefiere las mañanas", contact="ct_marta"))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS), contact=Contact(id="ct_marta"))
    answered = await lookup(pool, embedder, on, a_lookup("recall", "mañanas"), quotas=Quotas())
    facts = answered["facts"]
    assert isinstance(facts, list)
    assert len(facts) == 1


@postgres
async def test_the_contact_the_model_names_is_never_the_contact_that_is_read(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await written(pool, scope, ARow(text="Prefiere las mañanas", contact="ct_other"))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS))
    request = a_lookup("recall", "mañanas", contact="ct_other")
    answered = await lookup(pool, embedder, on, request, quotas=Quotas())
    assert answered == {"facts": []}


@postgres
async def test_a_web_call_with_no_identity_finds_nothing_and_writes_no_entry(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS), channel="web")
    answered = await lookup(pool, embedder, on, a_lookup("recall", "alergia"), quotas=Quotas())
    assert answered == {"facts": []}
    assert await entries_of(store, log) == []


@postgres
async def test_an_agent_that_keeps_no_memory_recalls_nothing_and_writes_no_entry(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await written(pool, scope, ARow(text="Es alérgica a la penicilina"))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT))
    answered = await lookup(pool, embedder, on, a_lookup("recall", "alergia"), quotas=Quotas())
    assert answered == {"facts": []}
    assert await entries_of(store, log) == []


@postgres
async def test_a_memory_that_is_full_is_still_read_because_a_cap_is_about_keeping(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await written(pool, scope, ARow(text="Es alérgica a la penicilina"))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS))
    full = Quotas(memory_facts=1)
    answered = await lookup(pool, embedder, on, a_lookup("recall", "alergia"), quotas=full)
    facts = answered["facts"]
    assert isinstance(facts, list)
    assert len(facts) == 1


@postgres
async def test_a_plan_with_no_memory_answers_an_empty_object_and_writes_no_entry(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await written(pool, scope, ARow(text="Es alérgica a la penicilina"))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS))
    off = Quotas(memory_facts=0)
    answered = await lookup(pool, embedder, on, a_lookup("recall", "alergia"), quotas=off)
    assert answered == {"facts": []}
    assert await entries_of(store, log) == []


# ── search ──


@postgres
async def test_search_answers_chunks_under_the_declarations_own_k_and_writes_the_sources(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await knowledge.put(
        pool, embedder, scope, Push("clinica", (cut(TARIFAS), cut(HORARIOS)), PUSHED_AT)
    )
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, bases=(Docs(base="clinica", k=1),)))
    answered = await lookup(pool, embedder, on, a_lookup("search", "la revisión"), quotas=Quotas())
    chunks = answered["chunks"]
    assert isinstance(chunks, list)
    assert len(chunks) == 1
    first = chunks[0]
    assert isinstance(first, dict)
    heading = HEADING_SEPARATOR.join(("Tarifas", "Revisión"))
    assert (first["path"], first["heading"]) == ("tarifas.md", heading)
    assert first["text"] == "La revisión cuesta cuarenta euros."
    (entry,) = await entries_of(store, log)
    assert entry.type == "docs.sources"
    assert entry.data["query"] == "la revisión"
    sources = entry.data["sources"]
    assert isinstance(sources, list)
    assert len(sources) == 1
    source = sources[0]
    assert isinstance(source, dict)
    assert (source["base"], source["excerpt"]) == ("clinica", "La revisión cuesta cuarenta euros.")


@postgres
async def test_a_class_searching_for_itself_may_say_how_many(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await knowledge.put(
        pool, embedder, scope, Push("clinica", (cut(TARIFAS), cut(HORARIOS)), PUSHED_AT)
    )
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, bases=(Docs(base="clinica", k=1),)))
    request = a_lookup("search", "revisión horarios", k=2)
    answered = await lookup(pool, embedder, on, request, quotas=Quotas())
    chunks = answered["chunks"]
    assert isinstance(chunks, list)
    assert len(chunks) == 2


@postgres
async def test_every_attached_base_is_searched_in_one_pass_so_the_scores_are_comparable(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await knowledge.put(pool, embedder, scope, Push("tarifas", (cut(TARIFAS),), PUSHED_AT))
    await knowledge.put(pool, embedder, scope, Push("horarios", (cut(HORARIOS),), PUSHED_AT))
    log = Log(store, call, AGENT)
    both = (Docs(base="tarifas", k=1), Docs(base="horarios", k=3))
    on = a_call(log, scope, AgentConfig(slug=AGENT, bases=both))
    request = a_lookup("search", "revisión horarios")
    answered = await lookup(pool, embedder, on, request, quotas=Quotas())
    chunks = answered["chunks"]
    assert isinstance(chunks, list)
    assert sorted(str(chunk["path"]) for chunk in chunks if isinstance(chunk, dict)) == [
        "horarios.md",
        "tarifas.md",
    ]
    (entry,) = await entries_of(store, log)
    assert entry.type == "docs.sources"


@postgres
async def test_the_declarations_min_score_is_what_the_base_is_searched_under(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await knowledge.put(
        pool, embedder, scope, Push("clinica", (cut(TARIFAS), cut(HORARIOS)), PUSHED_AT)
    )
    log = Log(store, call, AGENT)
    # The best chunk scores 1.0 relative to itself; a second one, less.
    on = a_call(log, scope, AgentConfig(slug=AGENT, bases=(Docs(base="clinica", min_score=1.0),)))
    answered = await lookup(pool, embedder, on, a_lookup("search", "la revisión"), quotas=Quotas())
    chunks = answered["chunks"]
    assert isinstance(chunks, list)
    assert len(chunks) == 1


@postgres
async def test_an_agent_that_declared_no_docs_finds_nothing_and_searches_nothing(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, meanings: Meanings
) -> None:
    log = Log(store, "CA_no_docs", AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT))
    answered = await lookup(pool, embedder, on, a_lookup("search", "la revisión"), quotas=Quotas())
    assert answered == {"chunks": []}
    assert await entries_of(store, log) == []
    assert meanings.inputs() == []


@postgres
async def test_a_plan_with_no_knowledge_base_answers_an_empty_object_and_writes_no_entry(
    pool: Pool, store: Store, scope: Scope, embedder: Embedder, call: str
) -> None:
    await knowledge.put(pool, embedder, scope, Push("clinica", (cut(TARIFAS),), PUSHED_AT))
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, bases=READS))
    off = Quotas(knowledge_chunks=0)
    answered = await lookup(pool, embedder, on, a_lookup("search", "la revisión"), quotas=off)
    assert answered == {"chunks": []}
    assert await entries_of(store, log) == []


# ── when nothing can run ──


@postgres
async def test_a_box_with_no_embedder_answers_every_lookup_with_nothing_found(
    pool: Pool, store: Store, scope: Scope, call: str
) -> None:
    log = Log(store, call, AGENT)
    on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS, bases=READS))
    recalled = await lookup(pool, None, on, a_lookup("recall", "alergia"), quotas=Quotas())
    searched = await lookup(pool, None, on, a_lookup("search", "revisión"), quotas=Quotas())
    assert (recalled, searched) == ({"facts": []}, {"chunks": []})
    assert await entries_of(store, log) == []


@postgres
async def test_a_down_embedder_finds_nothing_and_the_error_entry_names_the_tool(
    pool: Pool, store: Store, scope: Scope, call: str
) -> None:
    down = Embeddings(script=[httpx.ConnectError("nothing listens")] * 4)
    async with httpx.AsyncClient(transport=down.transport()) as http:
        embedder = Embedder(MEANING, "a-key", http)
        log = Log(store, call, AGENT)
        on = a_call(log, scope, AgentConfig(slug=AGENT, memory=KEEPS))
        answered = await lookup(pool, embedder, on, a_lookup("recall", "alergia"), quotas=Quotas())
    assert answered == {"facts": []}
    (entry,) = await entries_of(store, log)
    assert entry.type == "error"
    assert (entry.data["code"], entry.data["recoverable"]) == ("recall_skipped", True)
    assert "recall did not run" in str(entry.data["message"])


# ── what a hang-up reads ──


def test_the_turns_of_a_call_are_what_memory_reads_and_tools_are_named() -> None:
    log_name = "CA_heard"
    route = Route(org="org_a", agent=AGENT, channel="phone", number="+59829001199", env="sandbox")
    context = CallContext(
        call=log_name,
        channel="phone",
        direction="inbound",
        caller=CONTACT,
        route=route,
        today=date(2026, 9, 28),
    )
    config = AgentConfig(
        slug=AGENT,
        memory=KEEPS,
        tools=(ToolSpec("book_slot", "Books a slot.", {"type": "object"}),),
    )
    entries = [
        _entry(1, "call.started", {}),
        _entry(2, "turn.user", {"speech_id": "sp_1", "text": "Hola, soy Marta", "metrics": {}}),
        _entry(3, "tool.call", {"call_id": "t1", "name": "book_slot", "arguments": {}}),
        _entry(
            4,
            "turn.agent",
            {"speech_id": "sp_1", "text": "Hola Marta", "interrupted": False, "metrics": {}},
        ),
    ]
    heard = heard_in(context, config, entries, at=HUNG_UP)
    assert heard is not None
    assert (heard.contact, heard.call, heard.channel, heard.at) == (
        CONTACT,
        log_name,
        "phone",
        HUNG_UP,
    )
    assert [(turn.role, turn.text) for turn in heard.spoken] == [
        ("user", "Hola, soy Marta"),
        ("agent", "Hola Marta"),
    ]
    assert heard.tools == ("book_slot",)


def test_nothing_is_read_for_an_agent_with_no_policy_or_a_caller_with_no_name() -> None:
    phone = Route(org="org_a", agent=AGENT, channel="phone", number="+59829001199", env="sandbox")
    web = Route(org="org_a", agent=AGENT, channel="web", env="sandbox")
    named = CallContext(
        call="CA_a",
        channel="phone",
        direction="inbound",
        caller=CONTACT,
        route=phone,
        today=NOW.date(),
    )
    nobody = CallContext(
        call="CA_b", channel="web", direction="inbound", caller="web_x", route=web, today=NOW.date()
    )
    assert heard_in(named, AgentConfig(slug=AGENT), [], at=HUNG_UP) is None
    assert heard_in(nobody, AgentConfig(slug=AGENT, memory=KEEPS), [], at=HUNG_UP) is None


def _entry(seq: int, kind: str, data: JsonObject) -> Entry:
    return Entry(
        seq=seq, ts=float(seq), call="CA_heard", agent=AGENT, type=kind, ephemeral=False, data=data
    )
