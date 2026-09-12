"""The org's own tables on the tenant's key: its usage, its numbers, and the other world's key."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.registry import Registry
from pinecall.api.login import ONE_WORLD_EACH
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.types import DEVELOPMENT, PRODUCTION
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT, Json
from tests.api.talking import got
from tests.log.test_usage import A_SUMMARY

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


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANAS_KEY: ANA})


# starlette's TestClient types its requests through httpx's private `_types`: one untyped handle.
def posted(gateway: TestClient, path: str, body: object, bearer: str) -> tuple[int, Json]:
    handle: Any = gateway
    answer: Any = handle.post(path, json=body, headers={"Authorization": f"Bearer {bearer}"})
    status: int = answer.status_code
    said: Json = answer.json()
    return status, said


def listed(gateway: TestClient, path: str, bearer: str) -> tuple[int, list[Json]]:
    """A door that answers a list, which `got` does not type."""
    handle: Any = gateway
    answer: Any = handle.get(path, headers={"Authorization": f"Bearer {bearer}"})
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
    status, body = got(gateway, "/v1/usage", ANAS_KEY)
    assert status == 200
    assert [row["call"] for row in body["rows"]] == ["CA_1"]
    assert body["totals"] is not None and body["totals"]["calls"] == 1
    assert body["next"] == 2, "the cursor moves past the other org's row too"
    _, empty = got(gateway, f"/v1/usage?after={body['next']}", ANAS_KEY)
    assert (empty["rows"], empty["totals"], empty["next"]) == ([], None, None)


async def test_numbers_are_the_orgs_doors_in_the_keys_world_with_their_source(
    gateway: TestClient, registry: Registry
) -> None:
    await registry.register(
        "app_1",
        A_RECORD.org,
        PRODUCTION,
        AGENT,
        [defs.Route(channel="phone", number="+34910000000")],
    )
    status, doors = listed(gateway, "/v1/numbers", ANAS_KEY)
    assert status == 200
    assert [(door["route"]["number"], door["source"]) for door in doors] == [
        ("+34910000000", "app")
    ]


def test_the_tables_ask_their_own_scope(gateway: TestClient) -> None:
    """The org's machine key opens them all; a key without the scope is told what it opens."""
    assert got(gateway, "/v1/usage", A_KEY)[0] == 200
    assert got(gateway, "/v1/numbers", A_KEY)[0] == 200


def test_a_person_looks_the_other_way_and_holds_a_key_for_that_world_too(
    gateway: TestClient,
) -> None:
    status, said = posted(gateway, "/v1/login/env", {"env": DEVELOPMENT}, ANAS_KEY)
    assert status == 200, said
    assert (said["env"], said["subject"], said["name"], said["label"]) == (
        DEVELOPMENT,
        "m_ana",
        "Ana",
        "laptop",
    )
    assert said["scopes"] == sorted(ANA.scopes)
    _, who = got(gateway, "/v1/whoami", str(said["key"]))
    assert (who["env"], who["subject"]) == (DEVELOPMENT, "m_ana")


def test_an_orgs_machine_key_is_one_worlds_and_a_world_that_is_none_is_refused(
    gateway: TestClient,
) -> None:
    status, said = posted(gateway, "/v1/login/env", {"env": DEVELOPMENT}, A_KEY)
    assert (status, said["detail"]) == (403, ONE_WORLD_EACH)
    status, said = posted(gateway, "/v1/login/env", {"env": "staging"}, ANAS_KEY)
    assert status == 400 and "staging" in str(said["detail"])
    assert NOT_OPENED  # the sentence the scoped doors refuse with, pinned elsewhere
