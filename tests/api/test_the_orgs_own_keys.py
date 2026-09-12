"""/v1/keys: an org issues the key its own server runs on, reads them back, and revokes one."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.keys import NO_SUCH_KEY, NOT_YOURS_TO_GIVE
from pinecall.auth.keys import KeyRecord, MemoryKeys, fingerprint
from pinecall.types import DEVELOPMENT, PRODUCTION
from tests.api.conftest import A_KEY, A_RECORD, Json
from tests.api.talking import got

pytestmark = pytest.mark.unit

# Diego runs the floor: the org's keys and numbers are his, the app socket never was. He is the
# reason the door checks what the asking key opens before it mints anything.
DIEGOS_KEY = "pk_test_diegos_laptop"
DIEGO = KeyRecord(
    key_id="k_diego",
    org=A_RECORD.org,
    label="laptop",
    scopes=frozenset({"calls", "keys", "numbers"}),
    subject="m_diego",
    name="Diego",
)

# Another tenant entirely, so a fingerprint that is real and is not this org's has a row to be.
ANOTHERS_KEY = "pk_test_another_tenant"
ANOTHER = KeyRecord(key_id="k_other", org="tienda-sur", label="their box")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, DIEGOS_KEY: DIEGO, ANOTHERS_KEY: ANOTHER})


def posted(gateway: TestClient, path: str, body: object, bearer: str) -> tuple[int, Json]:
    """starlette's TestClient types its requests through httpx's private `_types`: one handle."""
    handle: Any = gateway
    answer: Any = handle.post(path, json=body, headers={"Authorization": f"Bearer {bearer}"})
    status: int = answer.status_code
    said: Json = answer.json()
    return status, said


def listed(gateway: TestClient, bearer: str) -> tuple[int, list[Json]]:
    handle: Any = gateway
    answer: Any = handle.get("/v1/keys", headers={"Authorization": f"Bearer {bearer}"})
    status: int = answer.status_code
    rows: list[Json] = answer.json()
    return status, rows


def test_the_key_a_server_runs_on_holds_an_agent_names_nobody_and_opens_production(
    gateway: TestClient,
) -> None:
    """The default is the shape a deployment has: `app`, production, and no person on it."""
    status, said = posted(gateway, "/v1/keys", {"label": "prod server"}, A_KEY)
    assert status == 200, said
    assert (said["scopes"], said["env"], said["subject"]) == (["app"], PRODUCTION, None)
    assert (got(gateway, "/v1/whoami", str(said["key"]))[1])["label"] == "prod server"


def test_a_key_for_ci_opens_development_when_the_org_says_so(gateway: TestClient) -> None:
    body = {"label": "ci", "env": DEVELOPMENT, "scopes": ["app", "knowledge"]}
    status, said = posted(gateway, "/v1/keys", body, A_KEY)
    assert (status, said["env"], said["scopes"]) == (200, DEVELOPMENT, ["app", "knowledge"])


def test_a_key_cannot_hand_out_what_it_does_not_open_itself(gateway: TestClient) -> None:
    """Or the smallest role in an org would be a way to mint the largest."""
    status, said = posted(gateway, "/v1/keys", {"scopes": ["app", "team"]}, DIEGOS_KEY)
    assert status == 403
    assert said["detail"] == NOT_YOURS_TO_GIVE.format(missing="app · team")
    assert listed(gateway, DIEGOS_KEY)[1] == listed(gateway, A_KEY)[1], "and nothing was minted"


def test_the_listing_is_fingerprints_and_never_a_key(gateway: TestClient) -> None:
    _, issued = posted(gateway, "/v1/keys", {"label": "prod server"}, A_KEY)
    status, rows = listed(gateway, A_KEY)
    assert status == 200
    minted = next(row for row in rows if row["label"] == "prod server")
    assert minted["fingerprint"] == fingerprint(str(issued["key"]))
    assert str(issued["key"]) not in str(rows), "a key is written down nowhere but its sha256"


def test_the_org_reads_only_its_own_keys(gateway: TestClient) -> None:
    rows = listed(gateway, A_KEY)[1]
    assert {row["org"] for row in rows} == {A_RECORD.org}


def test_a_revoked_key_stops_opening_the_next_door(gateway: TestClient) -> None:
    _, issued = posted(gateway, "/v1/keys", {"label": "the stolen laptop"}, A_KEY)
    hashed = fingerprint(str(issued["key"]))
    assert got(gateway, "/v1/whoami", str(issued["key"]))[0] == 200
    status, said = posted(gateway, f"/v1/keys/{hashed}/revoke", None, A_KEY)
    assert (status, said) == (200, {"fingerprint": hashed, "revoked": True})
    assert got(gateway, "/v1/whoami", str(issued["key"]))[0] == 401


def test_revoking_twice_is_not_told_as_done_a_second_time(gateway: TestClient) -> None:
    _, issued = posted(gateway, "/v1/keys", {"label": "a laptop"}, A_KEY)
    hashed = fingerprint(str(issued["key"]))
    posted(gateway, f"/v1/keys/{hashed}/revoke", None, A_KEY)
    status, said = posted(gateway, f"/v1/keys/{hashed}/revoke", None, A_KEY)
    assert (status, said["detail"]) == (404, NO_SUCH_KEY.format(fingerprint=hashed))


def test_another_tenants_fingerprint_is_not_there_to_revoke(gateway: TestClient) -> None:
    """The same 404 a fingerprint nobody has gets: a tenant learns nothing about the table."""
    hashed = fingerprint(ANOTHERS_KEY)
    status, said = posted(gateway, f"/v1/keys/{hashed}/revoke", None, A_KEY)
    assert (status, said["detail"]) == (404, NO_SUCH_KEY.format(fingerprint=hashed))
    assert got(gateway, "/v1/whoami", ANOTHERS_KEY)[0] == 200, "and their key still opens doors"
