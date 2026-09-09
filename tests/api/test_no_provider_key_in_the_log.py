"""The rule the feature rests on: a stored provider key reaches the worker's door and no other."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from pinecall.log.store import MemoryStore
from pinecall.types import ProviderKeys
from pinecall_protocol import decode_entries
from pinecall_protocol.fixtures import GOLDEN_LOG
from tests.api.conftest import (  # noqa: F401 — imported so pytest collects them here
    A_KEY,
    AGENT,
    AN_ORG,
    a_call_the_app_ends,
    admission,
    an_app,
    declared,
    gateway,
    graph,
    keys,
    keys_asked,
    live,
    llm,
    llms,
    logs,
    models_asked,
    ops_http,
    orgs,
    overrides,
    registry,
    routes,
    settings,
    snapshots,
    store,
    threads,
    tokens,
    vault,
    wired,
)

pytestmark = pytest.mark.unit

# A key nobody could mistake for anything else, so a failure names itself in one grep. Every
# assertion in this file asks the same question: does this string appear where it must not.
CANARY = "sk-LEAKCANARY-if-you-read-this-outside-the-worker-door-it-leaked"

# The golden call this repo reduces on both sides, replayed into the store so the doors below
# answer with a whole call and not with a shape invented here. protocol/fixtures/.
CALL = "CA_8f4a2c"

# The one response body in the runtime that may carry a provider key — the worker's, on its own
# org's API key. Everything else in this file is the list of doors that must never.
THE_ONE_DOOR_THAT_MAY = f"/v1/agents/{AGENT}/provider-keys"

# pytest collects a fixture it finds in a test module's namespace; this list is what says so.
__all__ = [
    "admission",
    "gateway",
    "graph",
    "keys",
    "keys_asked",
    "live",
    "llm",
    "llms",
    "logs",
    "models_asked",
    "ops_http",
    "orgs",
    "overrides",
    "registry",
    "routes",
    "settings",
    "snapshots",
    "store",
    "threads",
    "tokens",
    "vault",
    "wired",
]


async def test_no_row_of_the_log_carries_the_key_the_org_brought(
    ops_http: httpx.AsyncClient,  # noqa: F811 — the fixture, by name
    gateway: TestClient,  # noqa: F811 — the fixture, by name
    store: MemoryStore,  # noqa: F811 — the fixture, by name
) -> None:
    """Criterion 2, the store half: every entry of the call's log and of the agent's own."""
    await _the_org_brought_its_own_key(ops_http)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        entries = await _a_golden_call(store)
        written = await _every_row_of(store, entries)
    assert "call.summary" in written, "the golden call was never written"
    assert CANARY not in written


async def test_no_door_but_the_workers_own_ever_answers_with_it(
    ops_http: httpx.AsyncClient,  # noqa: F811 — the fixture, by name
    gateway: TestClient,  # noqa: F811 — the fixture, by name
    store: MemoryStore,  # noqa: F811 — the fixture, by name
) -> None:
    """Criterion 2, the wire half: every door a tenant, a browser or the console reads."""
    await _the_org_brought_its_own_key(ops_http)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        await _a_golden_call(store)
        answered = {door: _read(gateway, door) for door in _the_doors_that_must_not()}
        assert all(body for body in answered.values()), f"a door said nothing: {answered}"
        leaked = [door for door, body in answered.items() if CANARY in body]
        assert not leaked, f"the key came back from {leaked}"
        assert CANARY in _read(gateway, THE_ONE_DOOR_THAT_MAY)


# The third door a stored key reaches, and the one this card opened: a written call now asks the
# vault as the socket opens, exactly as the worker asks it before a voice call. Everything the
# session goes on to write — its own log, its summary, the doors that read it back — is scanned
# for the canary the same way the replayed golden is above.
async def test_no_row_of_a_text_call_on_the_orgs_own_key_carries_it(
    ops_http: httpx.AsyncClient,  # noqa: F811 — the fixture, by name
    gateway: TestClient,  # noqa: F811 — the fixture, by name
    store: MemoryStore,  # noqa: F811 — the fixture, by name
    keys_asked: list[ProviderKeys],  # noqa: F811 — the fixture, by name
) -> None:
    """Criterion 2, the text half: a chat session runs on the key and writes it down nowhere."""
    await _the_org_brought_its_own_key(ops_http)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        call = a_call_the_app_ends(gateway, app_socket)
        # As the last line of the door test above: a scan that found nothing because nothing was
        # ever asked for would prove nothing at all.
        assert keys_asked[-1] == {"elevenlabs": CANARY}
        written = await _every_row_of_the_call(store, call)
        answered = {door: _read(gateway, door) for door in _the_doors_that_must_not(call)}
    assert "call.summary" in written, "the text call was never written"
    assert CANARY not in written
    leaked = [door for door, body in answered.items() if CANARY in body]
    assert not leaked, f"the key came back from {leaked}"


async def _the_org_brought_its_own_key(ops_http: httpx.AsyncClient) -> None:
    """The clinic's own ElevenLabs key, kept through the operator's door."""
    door = f"/v1/ops/orgs/{AN_ORG.slug}/provider-keys/elevenlabs"
    kept = await ops_http.put(door, json={"key": CANARY})
    assert kept.status_code == 204, kept.text


async def _a_golden_call(store: MemoryStore) -> int:
    """The golden log, entry for entry, owned by the org whose key is in the vault."""
    await store.owned(CALL, AGENT, AN_ORG.id)
    golden = decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))
    for entry in golden:
        await store.append(CALL, AGENT, entry.type, entry.data, entry.ephemeral)
    return len(golden)


async def _every_row_of(store: MemoryStore, entries: int) -> str:
    """Every entry this process wrote — the call's log and the agent's own — as one string."""
    written = await store.since(CALL)
    assert len(written) == entries, "the scan is paging and no longer reads the whole log"
    return await _every_row_of_the_call(store, CALL)


async def _every_row_of_the_call(store: MemoryStore, call: str) -> str:
    """One call's whole log and the agent's own beside it, as the one string a scan greps."""
    rows: list[Any] = [
        *(entry.__dict__ for entry in await store.since(call)),
        *(entry.__dict__ for entry in await store.agent_since(AGENT)),
    ]
    return json.dumps(rows, default=str)


def _the_doors_that_must_not(call: str = CALL) -> tuple[str, ...]:
    """Every door read with an org's API key, which is every door but the worker's own."""
    return (
        "/v1/routes",
        "/v1/agents",
        f"/v1/agents/{AGENT}/config",
        f"/v1/agents/{AGENT}/pipeline",
        f"/v1/agents/{AGENT}/calls",
        f"/v1/agents/{AGENT}/sessions",
        f"/v1/calls/{call}/events",
        f"/v1/calls/{call}/state",
    )


def _read(client: TestClient, door: str) -> str:
    """One GET with the org's own key, as the bytes a reader would actually receive."""
    handle: Any = client
    answer: Any = handle.get(door, headers={"Authorization": f"Bearer {A_KEY}"})
    assert answer.status_code == 200, f"{door}: {answer.status_code} {answer.text}"
    return str(answer.text)
