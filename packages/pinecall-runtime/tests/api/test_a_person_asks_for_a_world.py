"""An instance is one world: a request's header is held against it, and production is a gate."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.auth.bearer import POLICY_VIOLATION, close_reason
from pinecall.auth.env import (
    ENV_HEADER,
    NO_PRODUCTION,
    NOT_A_WORLD,
    NOT_THIS_WORLD,
    ONE_WORLD,
    SAY_THE_WORLD,
)
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.settings import Settings
from pinecall.types import PRODUCTION, SANDBOX, Member, Role
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS
from tests.api.talking import a_door, a_register, answering_in, got

pytestmark = pytest.mark.unit

# A door that opens a scope reads the key as it ACTS; whoami opens none and reads it as who it is.
AGENTS = "/v1/agents"
WHOAMI = "/v1/whoami"
SANDBOXS = "https://sandbox.clinica.test"
PRODUCTIONS = "https://box.clinica.test"


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
    """The one key a login leaves them: no world of its own (auth/person_keys.py)."""
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
# A server's token made in the sandbox: it opens that world and no other.
A_SANDBOX_TOKEN = "pc_test_the_ci_job"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Production's instance, told where the sandbox answers so the sentences can say it."""
    return settings.model_copy(update={"elsewhere_url": SANDBOXS})


@pytest.fixture
def keys() -> MemoryKeys:
    ghost = KeyRecord(key_id="k_ghost", org=A_RECORD.org, subject="m_nobody", name="Nobody")
    ci = KeyRecord(key_id="k_ci", org=A_RECORD.org, env=SANDBOX, label="ci")
    held = {key: a_persons_key(member) for key, member in KEYS.items()}
    return MemoryKeys({A_KEY: A_RECORD, GHOSTS_KEY: ghost, A_SANDBOX_TOKEN: ci, **held})


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers([CARLA, DIANA, ANA])


def the_sandbox_answers(settings: Settings) -> Settings:
    """The sandbox's instance from here on, told where production answers."""
    return answering_in(SANDBOX, settings.model_copy(update={"elsewhere_url": PRODUCTIONS}))


def asked(gateway: TestClient, path: str, key: str, world: str | None = None) -> tuple[int, Any]:
    return got(gateway, path, key, world)


# ── the header is an assertion about the instance ──────────────────────────────


def test_a_person_who_says_no_world_at_production_is_told_where_the_sandbox_is(
    gateway: TestClient,
) -> None:
    """No header meant the sandbox until the sandbox was an instance of its own: a CLI that
    predates it would now write production without knowing, so it is refused and told why."""
    status, said = asked(gateway, AGENTS, "pc_diana")
    assert (status, said["detail"]) == (403, SAY_THE_WORLD.format(elsewhere=SANDBOXS))


def test_a_person_who_says_no_world_at_the_sandbox_works_there_as_anywhere(
    gateway: TestClient, settings: Settings
) -> None:
    the_sandbox_answers(settings)
    assert asked(gateway, AGENTS, "pc_carla")[0] == 200
    status, who = asked(gateway, WHOAMI, "pc_carla")
    assert (status, who["env"]) == (200, SANDBOX)


def test_a_header_naming_the_other_world_is_refused_at_either_instance_with_where_it_is(
    gateway: TestClient, settings: Settings
) -> None:
    status, said = asked(gateway, WHOAMI, "pc_diana", SANDBOX)
    assert (status, said["detail"]) == (
        403,
        NOT_THIS_WORLD.format(here=PRODUCTION, asked=SANDBOX, elsewhere=SANDBOXS),
    )
    the_sandbox_answers(settings)
    status, said = asked(gateway, AGENTS, "pc_diana", PRODUCTION)
    assert (status, said["detail"]) == (
        403,
        NOT_THIS_WORLD.format(here=SANDBOX, asked=PRODUCTION, elsewhere=PRODUCTIONS),
    )


def test_a_world_that_is_neither_is_refused_by_the_word(gateway: TestClient) -> None:
    status, said = asked(gateway, AGENTS, "pc_diana", "staging")
    assert (status, said["detail"]) == (403, NOT_A_WORLD.format(asked="staging"))


def test_a_servers_token_of_the_other_world_is_refused_whatever_it_says(
    gateway: TestClient,
) -> None:
    status, said = asked(gateway, WHOAMI, A_SANDBOX_TOKEN)
    assert (status, said["detail"]) == (
        403,
        ONE_WORLD.format(world=SANDBOX, here=PRODUCTION, elsewhere=SANDBOXS),
    )
    assert asked(gateway, WHOAMI, A_KEY, PRODUCTION)[1]["env"] == PRODUCTION


# ── production is a gate on what a person does, not on who they are ────────────


def test_production_opens_for_the_person_the_org_let_in_and_for_nobody_else(
    gateway: TestClient,
) -> None:
    status, said = asked(gateway, AGENTS, "pc_carla", PRODUCTION)
    assert (status, said["detail"]) == (403, NO_PRODUCTION.format(name="Carla"))
    assert asked(gateway, AGENTS, "pc_diana", PRODUCTION)[0] == 200


def test_an_admin_opens_production_whatever_the_switch_says(gateway: TestClient) -> None:
    assert ANA.production is False
    assert asked(gateway, AGENTS, "pc_ana", PRODUCTION)[0] == 200


def test_taking_production_away_closes_the_very_next_request(
    gateway: TestClient, members: MemoryMembers
) -> None:
    assert asked(gateway, AGENTS, "pc_diana", PRODUCTION)[0] == 200
    asyncio.run(members.update(A_RECORD.org, DIANA.id, production=False))
    status, said = asked(gateway, AGENTS, "pc_diana", PRODUCTION)
    assert (status, said["detail"]) == (403, NO_PRODUCTION.format(name="Diana"))


def test_a_person_the_table_no_longer_has_opens_no_production(gateway: TestClient) -> None:
    status, said = asked(gateway, AGENTS, GHOSTS_KEY, PRODUCTION)
    assert (status, said["detail"]) == (403, NO_PRODUCTION.format(name="Nobody"))


def test_a_person_kept_out_of_production_still_learns_who_they_are_there(
    gateway: TestClient,
) -> None:
    """Whoami, the login code, pairing and the org switch read the key as an identity: without
    them a developer the org keeps out of production could never be handed to the sandbox."""
    status, who = asked(gateway, WHOAMI, "pc_carla")
    assert (status, who["name"], who["env"], who["production"]) == (
        200,
        "Carla",
        PRODUCTION,
        False,
    )
    handle: Any = gateway
    code = handle.post("/v1/login/codes", headers={"Authorization": "Bearer pc_carla"})
    assert code.status_code == 200, code.text


# ── the sockets read the key as it acts ───────────────────────────────────────


def test_a_person_with_production_holds_the_agent_there_from_their_own_key(
    gateway: TestClient,
) -> None:
    """`pinecall start --prod`: the app socket in production, on the person's key."""
    headers = {"Authorization": "Bearer pc_diana", ENV_HEADER: PRODUCTION}
    with gateway.websocket_connect(APPS, headers=headers) as socket:
        socket.send_json(a_register(AGENT, a_door("web")))
        assert socket.receive_json()["type"] == "agent.registered"


@pytest.mark.parametrize(
    ("said", "why"),
    [
        (PRODUCTION, NO_PRODUCTION.format(name="Carla")),
        (None, SAY_THE_WORLD.format(elsewhere=SANDBOXS)),
    ],
)
def test_the_app_socket_at_production_closes_on_a_person_it_may_not_serve_and_says_why(
    gateway: TestClient, said: str | None, why: str
) -> None:
    headers = {"Authorization": "Bearer pc_carla"} | ({} if said is None else {ENV_HEADER: said})
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        gateway.websocket_connect(APPS, headers=headers) as socket,
    ):
        socket.send_json(a_register(AGENT, a_door("web")))
        socket.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert refused.value.reason == close_reason(why)
