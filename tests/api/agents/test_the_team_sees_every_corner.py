"""`GET /v1/agents`: a developer sees their corner, an admin the team's, each row saying whose."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall._settings import Settings
from pinecall.auth.corner import CANNOT_LOOK_THERE, NOT_A_COLLEAGUE, looking_into
from pinecall.auth.keys import KeyRecord, MemoryKeys, held_by
from pinecall.auth.members_memory import MemoryMembers
from pinecall.log.store import MemoryStore
from pinecall.types import ROLE_SCOPES, SANDBOX, Member
from tests.api.calls.test_listing import RINGING, UP
from tests.api.conftest import A_RECORD, AGENT, APPS
from tests.api.talking import a_door, a_register, got
from tests.conftest import a_sandbox

pytestmark = pytest.mark.unit

BERNA, CARLA = "m_berna", "m_carla"
BERNAS_ADDRESS, CARLAS_ADDRESS = "berna@clinica.test", "carla@clinica.test"

# One org, three keys into the SAME world. The two laptops are people; the third is the admin's
# browser, which holds no agent of its own and is the one that has to see both.
BERNAS_KEY = "pk_test_bernas_laptop"
CARLAS_KEY = "pk_test_carlas_laptop"
AN_ADMINS_KEY = "pk_test_the_admins_browser"


def _a_person(key_id: str, subject: str, role: str) -> KeyRecord:
    return KeyRecord(
        key_id=key_id, org=A_RECORD.org, env=SANDBOX, subject=subject, scopes=ROLE_SCOPES[role]
    )


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The sandbox's instance: corners are the sandbox's, production has one."""
    return a_sandbox(settings)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {
            BERNAS_KEY: _a_person("k_berna", BERNA, "developer"),
            CARLAS_KEY: _a_person("k_carla", CARLA, "developer"),
            AN_ADMINS_KEY: _a_person("k_admin", "m_ana", "admin"),
        }
    )


@pytest.fixture
def members() -> MemoryMembers:
    """The two developers, so a row can say whose corner it is in words a person reads."""
    return MemoryMembers(
        [_a_person_row(BERNA, BERNAS_ADDRESS), _a_person_row(CARLA, CARLAS_ADDRESS)]
    )


def _a_person_row(id: str, email: str) -> Member:
    return Member(
        id=id, org=A_RECORD.org, email=email, name=email, role="developer", status="active"
    )


def _an_app_on(gateway: TestClient, key: str) -> WebSocketTestSession:
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {key}"})


def _listed(gateway: TestClient, key: str) -> list[dict[str, Any]]:
    status, body = got(gateway, "/v1/agents", key)
    assert status == 200
    return [dict(held) for held in body["agents"]]  # type: ignore[call-overload,index]


def test_a_developer_sees_their_own_corner_and_not_a_colleagues(gateway: TestClient) -> None:
    """Two laptops holding one slug: each terminal's console draws the agent it is running."""
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        assert _listed(gateway, BERNAS_KEY) == [
            {
                "slug": AGENT,
                "channels": ["web"],
                "holder": {"holder": BERNA, "name": BERNAS_ADDRESS},
            }
        ]
        assert _listed(gateway, CARLAS_KEY) == [
            {
                "slug": AGENT,
                "channels": ["web"],
                "holder": {"holder": CARLA, "name": CARLAS_ADDRESS},
            }
        ]


def test_an_admin_sees_every_corner_and_each_row_says_whose(gateway: TestClient) -> None:
    """Nobody could see this: a tenant's admin had no way to tell what the team was running.

    And the id alone would not have helped: `m_berna` names nobody, so the row carries the same
    `{holder, name}` the line door answers with.
    """
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        seen = _listed(gateway, AN_ADMINS_KEY)

        whose = sorted(str(dict(held["holder"])["name"]) for held in seen)  # type: ignore[arg-type]
        assert whose == [BERNAS_ADDRESS, CARLAS_ADDRESS]
        assert {str(held["slug"]) for held in seen} == {AGENT}


# starlette's TestClient types its requests through httpx's private `_types`, which no checker can
# resolve, so every request this file makes goes through here: the ignores live in one place.
def _asked(gateway: TestClient, path: str, headers: dict[str, str]) -> tuple[int, str, Any]:
    """One GET at the gateway: the status, the body as text, and the body as JSON when it is."""
    got: Any = gateway.get(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        path, headers=headers
    )
    status = int(got.status_code)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    text = str(got.text)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return status, text, json.loads(text) if text.startswith(("{", "[")) else None


def _the_line(gateway: TestClient, key: str, corner: str | None) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {key}"}
    if corner is not None:
        headers["pinecall-corner"] = corner
    status, _, said = _asked(gateway, f"/v1/agents/{AGENT}/line", headers)
    return status, said


def test_an_admin_opens_a_developers_copy_and_every_door_answers_in_that_corner(
    gateway: TestClient,
) -> None:
    """The console's agent panel: an admin picks Carla's copy, and the doors are Carla's."""
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        _, own = _the_line(gateway, AN_ADMINS_KEY, None)
        _, as_berna = _the_line(gateway, AN_ADMINS_KEY, BERNA)
        _, as_carla = _the_line(gateway, AN_ADMINS_KEY, CARLA)

    # Berna rang first and holds the line: it is hers when the admin looks from her corner only.
    assert (own["yours"], as_berna["yours"], as_carla["yours"]) == (False, True, False)


def test_a_developer_cannot_open_a_colleagues_copy(gateway: TestClient) -> None:
    status, body = _the_line(gateway, BERNAS_KEY, CARLA)
    assert (status, body["detail"]) == (403, CANNOT_LOOK_THERE)


def test_an_admin_names_only_a_member_of_the_org(gateway: TestClient) -> None:
    status, body = _the_line(gateway, AN_ADMINS_KEY, "m_nobody")
    assert (status, body["detail"]) == (403, NOT_A_COLLEAGUE)


def test_production_has_no_corner_to_open() -> None:
    admin = KeyRecord(key_id="k", org=A_RECORD.org, env="production", scopes=ROLE_SCOPES["admin"])
    with pytest.raises(PermissionError):
        looking_into(admin, CARLA)
    sandbox = KeyRecord(
        key_id="k", org=A_RECORD.org, env=SANDBOX, subject="m_ana", scopes=ROLE_SCOPES["admin"]
    )
    assert held_by(looking_into(sandbox, CARLA)) == CARLA


async def _a_call_in(store: MemoryStore, call: str, env: str, holder: str | None) -> None:
    """One call as a worker writes it, opened in this corner."""
    await store.owned(call, AGENT, A_RECORD.org, env, holder)
    await store.append(call, AGENT, "call.ringing", dict(RINGING))
    await store.append(call, AGENT, "call.started", dict(UP))


def _sessions(gateway: TestClient, key: str, corner: str | None = None) -> list[str]:
    headers = {"Authorization": f"Bearer {key}"}
    if corner is not None:
        headers["pinecall-corner"] = corner
    status, text, said = _asked(gateway, f"/v1/agents/{AGENT}/sessions", headers)
    assert status == 200, text
    calls: list[dict[str, Any]] = said["calls"]
    return [str(line["call"]) for line in calls]


async def test_each_developer_lists_their_own_calls_and_the_telephones_are_productions(
    gateway: TestClient, store: MemoryStore
) -> None:
    """One Sessions screen listed every world's and every developer's calls as one pile
    (2026-09-16): Berna's chat beside Carla's beside the telephone's."""
    with _an_app_on(gateway, BERNAS_KEY) as bernas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        await _a_call_in(store, "CA_phone", "production", None)
        await _a_call_in(store, "CA_berna", SANDBOX, BERNA)
        await _a_call_in(store, "CA_carla", SANDBOX, CARLA)

        assert _sessions(gateway, BERNAS_KEY) == ["CA_berna"]
        assert _sessions(gateway, CARLAS_KEY) == ["CA_carla"]
        # The admin's sandbox key reads its own corner, empty, and a colleague's when it names one.
        assert _sessions(gateway, AN_ADMINS_KEY) == []
        assert _sessions(gateway, AN_ADMINS_KEY, CARLA) == ["CA_carla"]
        # The org's floor door cuts the same way.
        status, floor = got(gateway, "/v1/sessions", BERNAS_KEY)
        assert (status, [line["call"] for line in floor["calls"]]) == (200, ["CA_berna"])
