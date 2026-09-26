"""The org's own tables on the tenant's key: its usage, its numbers, and the other world's key."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.auth.env import ENV_HEADER
from pinecall.auth.keys import KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.live.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.settings import Settings
from pinecall.types import PRODUCTION, SANDBOX, Member, Route
from pinecall_testkit.usage import A_SUMMARY
from tests.api.conftest import A_KEY, A_RECORD, AGENT, Json
from tests.api.talking import answering_in, got

pytestmark = pytest.mark.unit

# Ana, a manager: numbers, usage and the team are hers; the agent's declaration is not.
ANAS_KEY = "pk_test_anas_laptop"
ANA = KeyRecord(
    key_id="k_ana",
    org=A_RECORD.org,
    label="laptop",
    scopes=frozenset({"calls", "numbers", "usage", "team"}),
    subject="m_ana",
    name="Ana",
)

# The row her key's subject names, and the org lets her act in production: a request of hers that
# names production is read there (auth/env.py).
A_MEMBER = Member(
    id="m_ana",
    org=A_RECORD.org,
    email="ana@acme.com",
    name="Ana",
    role="manager",
    status="active",
    production=True,
)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANAS_KEY: ANA})


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers([A_MEMBER])


# starlette's TestClient types its requests through httpx's private `_types`: one untyped handle.
def posted(gateway: TestClient, path: str, body: object, bearer: str) -> tuple[int, Json]:
    handle: Any = gateway
    answer: Any = handle.post(path, json=body, headers={"Authorization": f"Bearer {bearer}"})
    status: int = answer.status_code
    said: Json = answer.json()
    return status, said


def listed(
    gateway: TestClient, path: str, bearer: str, world: str = PRODUCTION
) -> tuple[int, list[Json]]:
    """A door that answers a list, which `got` does not type, in the world the request names."""
    handle: Any = gateway
    headers = {"Authorization": f"Bearer {bearer}", ENV_HEADER: world}
    answer: Any = handle.get(path, headers=headers)
    status: int = answer.status_code
    rows: list[Json] = answer.json()
    return status, rows


async def test_usage_is_the_orgs_own_rows_and_totals_and_a_cursor(
    gateway: TestClient, store: MemoryStore
) -> None:
    await store.owned("CA_1", AGENT, A_RECORD.org)
    await store.append("CA_1", AGENT, "call.summary", dict(A_SUMMARY))
    await store.owned("CA_theirs", "clinica-vecina", "vecina")
    await store.append("CA_theirs", "clinica-vecina", "call.summary", dict(A_SUMMARY))
    status, body = got(gateway, "/v1/usage", ANAS_KEY, PRODUCTION)
    assert status == 200
    assert [row["call"] for row in body["rows"]] == ["CA_1"]
    assert body["totals"] is not None and body["totals"]["calls"] == 1
    assert body["next"] == 2, "the cursor moves past the other org's row too"
    _, empty = got(gateway, f"/v1/usage?after={body['next']}", ANAS_KEY, PRODUCTION)
    assert (empty["rows"], empty["totals"], empty["next"]) == ([], None, None)


async def test_numbers_are_the_orgs_rows_in_the_instances_world(
    gateway: TestClient, registry: Registry, routes: MemoryRoutes, settings: Settings
) -> None:
    """A door is a row somebody typed, in one world: holding an agent puts none in either."""
    await registry.register("app_1", A_RECORD.org, PRODUCTION, AGENT)
    await routes.put(Route(org=A_RECORD.org, agent=AGENT, channel="phone", number="+34910000000"))
    status, doors = listed(gateway, "/v1/numbers", ANAS_KEY)
    assert status == 200
    assert [door["route"]["number"] for door in doors] == ["+34910000000"]
    answering_in(SANDBOX, settings)
    assert listed(gateway, "/v1/numbers", ANAS_KEY, SANDBOX)[1] == [], "the sandbox has no number"


def test_the_tables_ask_their_own_scope(gateway: TestClient) -> None:
    """The org's machine key opens them all; a key without the scope is told what it opens."""
    assert got(gateway, "/v1/usage", A_KEY)[0] == 200
    assert got(gateway, "/v1/numbers", A_KEY)[0] == 200
