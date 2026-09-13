"""/v1/keys: an org issues the key its own server runs on, reads them back, and revokes one."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.keys import A_PERSON, NO_SUCH_KEY, NOT_YOURS_TO_GIVE, THIS_KEY
from pinecall.auth.keys import KeyRecord, MemoryKeys, fingerprint
from pinecall.auth.members import MemoryMembers
from pinecall.types import DEVELOPMENT, HOLDING, PRODUCTION, Member, for_a_person
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

# Ana owns the org, and her production key is shaped the way every person's is there: her role's
# preset LESS `app`, because a person does not hold an agent in production. She is the whole
# reason the bound below is her role and not her key — see api/keys.py, A_PERSON.
ANA = Member(
    id="m_ana",
    org=A_RECORD.org,
    email="ana@clinica.test",
    name="Ana",
    role="admin",
    status="active",
)
ANAS_KEY = "pk_test_anas_laptop"
ANAS_PRODUCTION_KEY = KeyRecord(
    key_id="k_ana",
    org=A_RECORD.org,
    label="laptop",
    scopes=for_a_person(ANA.scopes, PRODUCTION),
    subject=ANA.id,
    name=ANA.name,
)

# Quim runs the floor: his role opens `keys`, so he reaches this door — and it opens no `app`,
# so the rule that stopped a smaller role minting a larger one still stands. A `qa` would not do
# here: their role does not open `keys` at all and they are refused by the door itself.
QUIM = Member(
    id="m_quim",
    org=A_RECORD.org,
    email="quim@clinica.test",
    name="Quim",
    role="manager",
    status="active",
)
QUIMS_KEY = "pk_test_quims_laptop"
QUIMS_PRODUCTION_KEY = KeyRecord(
    key_id="k_quim",
    org=A_RECORD.org,
    label="laptop",
    scopes=for_a_person(QUIM.scopes, PRODUCTION),
    subject=QUIM.id,
    name=QUIM.name,
)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {
            A_KEY: A_RECORD,
            DIEGOS_KEY: DIEGO,
            ANOTHERS_KEY: ANOTHER,
            ANAS_KEY: ANAS_PRODUCTION_KEY,
            QUIMS_KEY: QUIMS_PRODUCTION_KEY,
        }
    )


@pytest.fixture
def members() -> MemoryMembers:
    """Ana and Quim are rows; Diego deliberately is not, so his key falls back to itself."""
    return MemoryMembers([ANA, QUIM])


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
    assert said["detail"] == NOT_YOURS_TO_GIVE.format(missing="app · team", whose=THIS_KEY)
    assert listed(gateway, DIEGOS_KEY)[1] == listed(gateway, A_KEY)[1], "and nothing was minted"


# The door was shut on the one thing a tenant most needs it for: an admin's production key does
# not carry `app`, so measuring the ask against the KEY meant nobody in the org could mint the key
# their own server runs on — in either world — and only the box operator could. A tenant could not
# deploy at all. The bound is the person's role, which is what their org trusts them with.
def test_an_admin_mints_the_key_their_server_runs_on(gateway: TestClient) -> None:
    assert HOLDING not in ANAS_PRODUCTION_KEY.scopes, "her own key does not hold an agent"
    status, said = posted(gateway, "/v1/keys", {"label": "el server"}, ANAS_KEY)
    assert status == 200, said
    assert (said["scopes"], said["env"], said["subject"]) == (["app"], PRODUCTION, None)


def test_a_role_that_does_not_open_a_door_still_cannot_hand_it_out(gateway: TestClient) -> None:
    """The rule stands; what changed is which of the asker's two sets it is measured against."""
    assert HOLDING not in QUIM.scopes
    status, said = posted(gateway, "/v1/keys", {"scopes": ["app"]}, QUIMS_KEY)
    assert status == 403
    assert said["detail"] == NOT_YOURS_TO_GIVE.format(missing="app", whose=A_PERSON)


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
