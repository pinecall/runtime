"""/v1/keys: a person makes the token a server runs on, the org keeps it, and it is revoked."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.keys import BY_A_PERSON, NO_SUCH_KEY, NOT_IN_PRODUCTION, SERVER_SCOPES
from pinecall.auth.keys import (
    NOT_OPENED,
    PRODUCTION_PREFIX,
    SANDBOX_PREFIX,
    KeyRecord,
    MemoryKeys,
    fingerprint,
)
from pinecall.auth.members_memory import MemoryMembers
from pinecall.types import PRODUCTION, SANDBOX, Member
from tests.api.conftest import A_KEY, A_RECORD, Json
from tests.api.talking import got

pytestmark = pytest.mark.unit

# Ana owns the clinic: an admin, so production is hers whatever her row says. Bruno writes the
# agent and the org keeps him out of production. Carla sits beside the calls and holds no agent.
ANA = Member(
    id="m_ana", org=A_RECORD.org, email="ana@x.test", name="Ana", role="admin", status="active"
)
BRUNO = Member(
    id="m_bruno",
    org=A_RECORD.org,
    email="bruno@x.test",
    name="Bruno",
    role="developer",
    status="active",
)
CARLA = Member(
    id="m_carla",
    org=A_RECORD.org,
    email="carla@x.test",
    name="Carla",
    role="supervisor",
    status="active",
)


def a_persons(member: Member) -> KeyRecord:
    """The key a login leaves this person: theirs, with their role's scopes, no world of its own."""
    return KeyRecord(
        key_id=f"k_{member.name.lower()}",
        org=member.org,
        label="laptop",
        env=SANDBOX,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
    )


ANAS_KEY = "pc_ana_laptop"
BRUNOS_KEY = "pc_bruno_laptop"
CARLAS_KEY = "pc_carla_laptop"
# Another tenant entirely, so a fingerprint that is real and is not this org's has a row to be.
ANOTHERS_KEY = "pc_live_another_tenant"
ANOTHER = KeyRecord(key_id="k_other", org="tienda-sur", label="their server")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {
            A_KEY: A_RECORD,
            ANAS_KEY: a_persons(ANA),
            BRUNOS_KEY: a_persons(BRUNO),
            CARLAS_KEY: a_persons(CARLA),
            ANOTHERS_KEY: ANOTHER,
        }
    )


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers([ANA, BRUNO, CARLA])


def posted(gateway: TestClient, path: str, body: object, bearer: str) -> tuple[int, Json]:
    """starlette's TestClient types its requests through httpx's private `_types`: one handle."""
    handle: Any = gateway
    answer: Any = handle.post(path, json=body, headers={"Authorization": f"Bearer {bearer}"})
    return int(answer.status_code), answer.json()


def listed(gateway: TestClient, bearer: str) -> list[Json]:
    handle: Any = gateway
    answer: Any = handle.get("/v1/keys", headers={"Authorization": f"Bearer {bearer}"})
    assert answer.status_code == 200, answer.text
    rows: list[Json] = answer.json()
    return rows


def a_token(gateway: TestClient, env: str, bearer: str = ANAS_KEY) -> Json:
    status, said = posted(gateway, "/v1/keys", {"label": "clinica-norte web", "env": env}, bearer)
    assert status == 200, said
    return said


def test_a_person_with_production_makes_the_token_a_server_runs_on_shown_once(
    gateway: TestClient,
) -> None:
    said = a_token(gateway, PRODUCTION)
    assert str(said["key"]).startswith(PRODUCTION_PREFIX)
    assert (said["env"], said["subject"], said["scopes"]) == (
        PRODUCTION,
        None,
        sorted(SERVER_SCOPES),
    )
    row = next(row for row in listed(gateway, ANAS_KEY) if row["label"] == "clinica-norte web")
    assert (row["kind"], row["env"], row["created_by"]) == ("server", PRODUCTION, "Ana")
    assert str(said["key"]) not in str(listed(gateway, ANAS_KEY)), "only its sha256 is kept"
    assert row["fingerprint"] == fingerprint(str(said["key"]))


def test_a_person_the_org_keeps_out_of_production_makes_a_sandbox_token_and_no_other(
    gateway: TestClient,
) -> None:
    status, said = posted(gateway, "/v1/keys", {"label": "prod", "env": PRODUCTION}, BRUNOS_KEY)
    assert (status, said["detail"]) == (403, NOT_IN_PRODUCTION.format(name="Bruno"))
    assert str(a_token(gateway, SANDBOX, BRUNOS_KEY)["key"]).startswith(SANDBOX_PREFIX)


def test_a_servers_token_makes_no_token_and_a_key_that_holds_no_agent_is_told_so(
    gateway: TestClient,
) -> None:
    status, said = posted(gateway, "/v1/keys", {"label": "x", "env": SANDBOX}, A_KEY)
    assert (status, said["detail"]) == (403, BY_A_PERSON)
    status, said = posted(gateway, "/v1/keys", {"label": "x", "env": SANDBOX}, CARLAS_KEY)
    assert status == 403
    assert said["detail"] == NOT_OPENED.format(scope="app", opens=" · ".join(sorted(CARLA.scopes)))


def test_the_token_is_the_orgs_and_outlives_the_person_who_made_it(
    gateway: TestClient, members: MemoryMembers
) -> None:
    said = a_token(gateway, PRODUCTION)
    asyncio.run(members.remove(A_RECORD.org, ANA.id))
    status, who = got(gateway, "/v1/whoami", str(said["key"]))
    assert (status, who["env"], who["production"]) == (200, PRODUCTION, True)


def test_a_person_sees_their_own_keys_and_every_servers_and_the_keys_scope_sees_all(
    gateway: TestClient,
) -> None:
    a_token(gateway, SANDBOX, BRUNOS_KEY)
    brunos = listed(gateway, BRUNOS_KEY)
    assert {row["name"] for row in brunos if row["kind"] == "person"} == {"Bruno"}
    assert any(row["kind"] == "server" for row in brunos)
    anas = listed(gateway, ANAS_KEY)
    assert {row["name"] for row in anas if row["kind"] == "person"} == {"Ana", "Bruno", "Carla"}


def test_the_maker_revokes_their_token_another_developer_cannot_and_an_admin_can(
    gateway: TestClient,
) -> None:
    brunos = fingerprint(str(a_token(gateway, SANDBOX, BRUNOS_KEY)["key"]))
    anas = fingerprint(str(a_token(gateway, SANDBOX)["key"]))
    status, said = posted(gateway, f"/v1/keys/{anas}/revoke", None, BRUNOS_KEY)
    assert (status, said["detail"]) == (404, NO_SUCH_KEY.format(fingerprint=anas))
    assert posted(gateway, f"/v1/keys/{brunos}/revoke", None, BRUNOS_KEY)[0] == 200
    assert posted(gateway, f"/v1/keys/{anas}/revoke", None, ANAS_KEY)[0] == 200


def test_a_revoked_token_stops_opening_the_next_door(gateway: TestClient) -> None:
    said = a_token(gateway, PRODUCTION)
    hashed = fingerprint(str(said["key"]))
    assert got(gateway, "/v1/whoami", str(said["key"]))[0] == 200
    status, answer = posted(gateway, f"/v1/keys/{hashed}/revoke", None, ANAS_KEY)
    assert (status, answer) == (200, {"fingerprint": hashed, "revoked": True})
    assert got(gateway, "/v1/whoami", str(said["key"]))[0] == 401
    status, answer = posted(gateway, f"/v1/keys/{hashed}/revoke", None, ANAS_KEY)
    assert (status, answer["detail"]) == (404, NO_SUCH_KEY.format(fingerprint=hashed))


def test_a_token_says_when_it_was_last_used(gateway: TestClient) -> None:
    said = a_token(gateway, PRODUCTION)
    row = next(row for row in listed(gateway, ANAS_KEY) if row["label"] == "clinica-norte web")
    assert row["last_used_at"] is None
    got(gateway, "/v1/whoami", str(said["key"]))
    row = next(row for row in listed(gateway, ANAS_KEY) if row["label"] == "clinica-norte web")
    assert row["last_used_at"] is not None


def test_another_tenants_fingerprint_is_not_there_to_revoke(gateway: TestClient) -> None:
    """The same 404 a fingerprint nobody has gets: a tenant learns nothing about the table."""
    hashed = fingerprint(ANOTHERS_KEY)
    status, said = posted(gateway, f"/v1/keys/{hashed}/revoke", None, ANAS_KEY)
    assert (status, said["detail"]) == (404, NO_SUCH_KEY.format(fingerprint=hashed))
    assert got(gateway, "/v1/whoami", ANOTHERS_KEY)[0] == 200, "and their key still opens doors"
