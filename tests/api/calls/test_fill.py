"""The two doors around a turn, knocked at by the worker's own client over the real ASGI app."""

from __future__ import annotations

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.types import markers_in
from pinecall.worker.client import Gateway, GatewayRefused
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app
from tests.api.talking import a_context
from tests.filling.fakes import ScriptedKnowledge, ScriptedMemory, a_chunk, a_fact

pytestmark = pytest.mark.unit

CALL = "call_the_worker_fills"
AN_OWNER = "app_the_fill_doors"
ANOTHER_KEY = "pk_test_the_shop_next_door"
ANOTHER_ORG = KeyRecord(key_id="k_2", org="tienda")

(RETRIEVED,) = markers_in('<!-- retrieved: {"k":1} -->')
(MEMORY,) = markers_in("<!-- memory: -->")

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
        AN_OWNER, A_RECORD.org, AGENT, [defs.Route(channel="phone", number="+34910000000")]
    )
    await registry.configure(AN_OWNER, AGENT, A_DECLARATION)


async def a_phone_call(worker_gateway: Gateway, registry: Registry) -> None:
    """The clinic declared, and one call from a number opened by the worker."""
    await declared(registry)
    await worker_gateway.opened(a_context(CALL, channel="phone", caller="+34600000001"), AGENT)


async def test_a_fill_answers_every_marker_and_writes_the_sources_on_the_calls_log(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    await a_phone_call(worker_gateway, registry)
    fills = await worker_gateway.fill(CALL, "¿cuánto cuesta?", [RETRIEVED, MEMORY], "sp_2")
    assert fills == {
        RETRIEVED.line: "### tarifas.md › Tarifas › Revisión\nLa revisión son 45 €.",
        MEMORY.line: "- prefiere turnos por la mañana",
    }
    written = {entry.type: entry.data for entry in await store.since(CALL)}
    assert written["docs.sources"]["speech_id"] == "sp_2"
    assert [one["id"] for one in written["docs.sources"]["sources"]] == ["c1"]
    assert written["memory.ops"]["ops"][0]["contact"] == "+34600000001"


async def test_a_call_nobody_opened_here_is_refused_in_the_events_doors_words(
    worker_gateway: Gateway,
) -> None:
    with pytest.raises(GatewayRefused, match="404.*open it with POST /v1/calls first"):
        await worker_gateway.fill("call_nobody_opened", "hola", [MEMORY], None)


async def test_another_orgs_worker_is_refused_the_call_in_the_very_same_words(
    worker_gateway: Gateway, registry: Registry
) -> None:
    """That the call exists at all is not the asker's business: 404, not 403."""
    await a_phone_call(worker_gateway, registry)
    http = over_the_asgi_app(f"Bearer {ANOTHER_KEY}")
    try:
        with pytest.raises(GatewayRefused, match="404.*open it with POST /v1/calls first"):
            await Gateway(http).fill(CALL, "hola", [MEMORY], None)
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
    """A gateway with no Postgres: the fill answers nothing, refuses nobody, writes no entry."""

    @pytest.fixture
    def knowledge(self) -> None:
        return None

    @pytest.fixture
    def memory(self) -> None:
        return None

    async def test_every_marker_is_filled_with_nothing(
        self, worker_gateway: Gateway, registry: Registry, store: MemoryStore
    ) -> None:
        await a_phone_call(worker_gateway, registry)
        fills = await worker_gateway.fill(CALL, "hola", [RETRIEVED, MEMORY], "sp_1")
        assert fills == {RETRIEVED.line: "", MEMORY.line: ""}
        assert await worker_gateway.remember(CALL) == 0
        assert [entry.type for entry in await store.since(CALL)] == ["call.ringing"]
