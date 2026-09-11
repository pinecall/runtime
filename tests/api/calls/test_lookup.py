"""The two doors around a turn, knocked at by the worker's own client over the real ASGI app."""

from __future__ import annotations

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.types import PRODUCTION
from pinecall.worker.client import Gateway, GatewayRefused
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app
from tests.api.talking import a_context
from tests.lookups.fakes import ScriptedKnowledge, ScriptedMemory, a_chunk, a_fact

pytestmark = pytest.mark.unit

CALL = "call_the_worker_looks_up"
AN_OWNER = "app_the_lookup_doors"
ANOTHER_KEY = "pk_test_the_shop_next_door"
ANOTHER_ORG = KeyRecord(key_id="k_2", org="tienda")

A_DECLARATION = defs.AgentConfig(
    docs=defs.DocsConfig(base="clinica"), memory=defs.MemoryConfig(remember=["preference"])
)


@pytest.fixture
def keys() -> MemoryKeys:
    """Two orgs' keys, so a worker of the shop next door can knock at the clinic's call."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_ORG})


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    """A base with one chunk about the tariff."""
    return ScriptedKnowledge(
        answers=[a_chunk("c1", "Tarifas › Revisión", "Tarifas › Revisión\n\nLa revisión son 45 €.")]
    )


@pytest.fixture
def memory() -> ScriptedMemory:
    """One fact about whoever calls."""
    return ScriptedMemory(answers=[a_fact("f1", "prefiere turnos por la mañana")])


async def declared(registry: Registry) -> None:
    """The clinic, holding a base and a memory policy, as its app would have declared it."""
    await registry.register(
        AN_OWNER,
        A_RECORD.org,
        PRODUCTION,
        AGENT,
        [defs.Route(channel="phone", number="+34910000000")],
    )
    await registry.configure(AN_OWNER, PRODUCTION, AGENT, A_DECLARATION)


async def a_phone_call(worker_gateway: Gateway, registry: Registry) -> None:
    """The clinic declared, and one call from a number opened by the worker."""
    await declared(registry)
    await worker_gateway.opened(a_context(CALL, channel="phone", caller="+34600000001"), AGENT)


async def test_a_lookup_answers_an_object_and_writes_the_sources_on_the_calls_log(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    await a_phone_call(worker_gateway, registry)
    found = await worker_gateway.lookup(CALL, "search", {"query": "¿cuánto cuesta?"}, "sp_2")
    assert found == {
        "chunks": [
            {"path": "tarifas.md", "heading": "Tarifas › Revisión", "text": "La revisión son 45 €."}
        ]
    }
    recalled = await worker_gateway.lookup(CALL, "recall", {"query": "hola"}, "sp_2")
    assert [fact["text"] for fact in recalled["facts"]] == ["prefiere turnos por la mañana"]
    written = {entry.type: entry.data for entry in await store.since(CALL)}
    assert written["docs.sources"]["speech_id"] == "sp_2"
    assert [one["id"] for one in written["docs.sources"]["sources"]] == ["c1"]
    assert written["memory.ops"]["ops"][0]["contact"] == "+34600000001"


async def test_a_call_nobody_opened_here_is_refused_in_the_events_doors_words(
    worker_gateway: Gateway,
) -> None:
    with pytest.raises(GatewayRefused, match="404.*open it with POST /v1/calls first"):
        await worker_gateway.lookup("call_nobody_opened", "recall", {"query": "hola"}, None)


async def test_another_orgs_worker_is_refused_the_call_in_the_very_same_words(
    worker_gateway: Gateway, registry: Registry
) -> None:
    """That the call exists at all is not the asker's business: 404, not 403."""
    await a_phone_call(worker_gateway, registry)
    http = over_the_asgi_app(f"Bearer {ANOTHER_KEY}")
    try:
        with pytest.raises(GatewayRefused, match="404.*open it with POST /v1/calls first"):
            await Gateway(http).lookup(CALL, "recall", {"query": "hola"}, None)
        with pytest.raises(GatewayRefused, match="404"):
            await Gateway(http).remember(CALL)
    finally:
        await http.aclose()


async def test_remember_reads_the_turns_off_the_log_and_answers_how_many_ops(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore, memory: ScriptedMemory
) -> None:
    await a_phone_call(worker_gateway, registry)
    await worker_gateway.append(
        CALL, "turn.user", {"speech_id": "sp_1", "text": "soy Ana", "metrics": {}}
    )
    assert await worker_gateway.remember(CALL) == 1
    [asked] = memory.remembered
    assert [turn.text for turn in asked["turns"]] == ["soy Ana"]
    assert asked["policy"].remember == ("preference",)
    types = [entry.type for entry in await store.since(CALL)]
    assert types == ["call.ringing", "turn.user", "memory.ops"]


class TestOnADevKey:
    """A gateway with no Postgres: a lookup finds nothing, refuses nobody, writes no entry."""

    @pytest.fixture
    def knowledge(self) -> None:
        return None

    @pytest.fixture
    def memory(self) -> None:
        return None

    async def test_every_lookup_finds_nothing_in_the_tools_own_shape(
        self, worker_gateway: Gateway, registry: Registry, store: MemoryStore
    ) -> None:
        await a_phone_call(worker_gateway, registry)
        assert await worker_gateway.lookup(CALL, "recall", {"query": "hola"}, "sp_1") == {
            "facts": []
        }
        assert await worker_gateway.lookup(CALL, "search", {"query": "hola"}, "sp_1") == {
            "chunks": []
        }
        assert await worker_gateway.remember(CALL) == 0
        assert [entry.type for entry in await store.since(CALL)] == ["call.ringing"]
