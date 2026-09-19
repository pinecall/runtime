"""A person holds one key and names the world per request; production opens if the org says so."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.auth.bearer import POLICY_VIOLATION
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.world import ENV_HEADER, NO_PRODUCTION, NOT_A_WORLD, ONE_WORLD
from pinecall.types import PRODUCTION, SANDBOX, Member, Role
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS
from tests.api.talking import a_door, a_register, got

pytestmark = pytest.mark.unit

WHOAMI = "/v1/whoami"


def a_member(name: str, role: Role, *, production: bool = False) -> Member:
    """One person of the clinic, active, with the switch the admin set for them."""
    return Member(
        id=f"m_{name.lower()}",
        org=A_RECORD.org,
        email=f"{name.lower()}@clinica.test",
        name=name,
        role=role,
        status="active",
        production=production,
    )


# Carla writes the agent and the org keeps her in the sandbox; Diana writes it and may act in
# production; Ana owns the org, and an admin opens production whatever the switch says.
CARLA = a_member("Carla", "developer")
DIANA = a_member("Diana", "developer", production=True)
ANA = a_member("Ana", "admin")


def a_persons_key(member: Member) -> KeyRecord:
    """The one key a login leaves them: no world of its own (auth/persons.py)."""
    return KeyRecord(
        key_id=f"k_{member.name.lower()}",
        org=member.org,
        env=SANDBOX,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
    )


KEYS = {f"pc_{member.name.lower()}": member for member in (CARLA, DIANA, ANA)}
# A key naming a person the table has no row for: what a removed member's key would leave.
GHOSTS_KEY = "pc_ghost"


@pytest.fixture
def keys() -> MemoryKeys:
    ghost = KeyRecord(key_id="k_ghost", org=A_RECORD.org, subject="m_nobody", name="Nobody")
    held = {key: a_persons_key(member) for key, member in KEYS.items()}
    return MemoryKeys({A_KEY: A_RECORD, GHOSTS_KEY: ghost, **held})


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers([CARLA, DIANA, ANA])


def whose(gateway: TestClient, key: str, world: str | None = None) -> tuple[int, Any]:
    return got(gateway, WHOAMI, key, world)


def test_a_request_that_names_no_world_runs_in_the_sandbox(gateway: TestClient) -> None:
    status, who = whose(gateway, "pc_carla")
    assert (status, who["env"], who["production"]) == (200, SANDBOX, False)


def test_production_opens_for_the_person_the_org_let_in_and_for_nobody_else(
    gateway: TestClient,
) -> None:
    status, who = whose(gateway, "pc_carla", PRODUCTION)
    assert (status, who["detail"]) == (403, NO_PRODUCTION.format(name="Carla"))
    status, who = whose(gateway, "pc_diana", PRODUCTION)
    assert (status, who["env"], who["production"]) == (200, PRODUCTION, True)


def test_an_admin_opens_production_whatever_the_switch_says(gateway: TestClient) -> None:
    assert ANA.production is False
    status, who = whose(gateway, "pc_ana", PRODUCTION)
    assert (status, who["env"]) == (200, PRODUCTION)


def test_taking_production_away_closes_the_very_next_request(
    gateway: TestClient, members: MemoryMembers
) -> None:
    assert whose(gateway, "pc_diana", PRODUCTION)[0] == 200
    asyncio.run(members.update(A_RECORD.org, DIANA.id, production=False))
    status, who = whose(gateway, "pc_diana", PRODUCTION)
    assert (status, who["detail"]) == (403, NO_PRODUCTION.format(name="Diana"))


def test_a_person_the_table_no_longer_has_opens_no_production(gateway: TestClient) -> None:
    status, who = whose(gateway, GHOSTS_KEY, PRODUCTION)
    assert (status, who["detail"]) == (403, NO_PRODUCTION.format(name="Nobody"))


def test_a_servers_token_stays_in_the_world_it_was_made_for(gateway: TestClient) -> None:
    assert whose(gateway, A_KEY, PRODUCTION)[1]["env"] == PRODUCTION
    status, who = whose(gateway, A_KEY, SANDBOX)
    assert (status, who["detail"]) == (403, ONE_WORLD.format(world=PRODUCTION, asked=SANDBOX))


def test_a_world_that_is_neither_is_refused_by_the_word(gateway: TestClient) -> None:
    status, who = whose(gateway, "pc_diana", "staging")
    assert (status, who["detail"]) == (403, NOT_A_WORLD.format(asked="staging"))


def test_a_person_with_production_holds_the_agent_there_from_their_own_key(
    gateway: TestClient,
) -> None:
    """`pinecall start --prod`: the app socket in production, on the person's key."""
    headers = {"Authorization": "Bearer pc_diana", ENV_HEADER: PRODUCTION}
    with gateway.websocket_connect(APPS, headers=headers) as socket:
        socket.send_json(a_register(AGENT, a_door("web")))
        assert socket.receive_json()["type"] == "agent.registered"


def test_the_app_socket_closes_on_a_person_kept_out_of_production_and_says_why(
    gateway: TestClient,
) -> None:
    headers = {"Authorization": "Bearer pc_carla", ENV_HEADER: PRODUCTION}
    with pytest.raises(WebSocketDisconnect) as refused:
        with gateway.websocket_connect(APPS, headers=headers) as socket:
            socket.send_json(a_register(AGENT, a_door("web")))
            socket.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert refused.value.reason == NO_PRODUCTION.format(name="Carla")
