"""The line doors: whose terminal a ring lands in, and the claim a second developer has to make."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.ops.peers import get_production_peer
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.settings import Settings
from pinecall.types import PRODUCTION, SANDBOX, Member, Route
from tests.api.conftest import A_RECORD, AGENT, over_the_asgi_app
from tests.api.peering import A_PEER_KEY, Scripting
from tests.api.talking import answering_in
from tests.conftest import a_sandbox

pytestmark = pytest.mark.unit

LINE = f"/v1/agents/{AGENT}/line"

BERNAS_KEY = "pk_test_bernas_laptop"
CARLAS_KEY = "pk_test_carlas_laptop"
BERNA = "m_berna"
CARLA = "m_carla"

BERNAS_SOCKET = "app_bernas_laptop"
CARLAS_SOCKET = "app_carlas_laptop"

A_DEV_NUMBER = "+59829001199"


def a_laptop(key_id: str, subject: str) -> KeyRecord:
    """A person's sandbox key: their corner is the member it was minted for."""
    return KeyRecord(key_id=key_id, org=A_RECORD.org, env=SANDBOX, subject=subject)


def a_member(id: str, email: str) -> Member:
    return Member(id=id, org=A_RECORD.org, email=email, name=email, role="developer")


# CI's: a sandbox key that names nobody, which is what holds the org's own corner.
CI_KEY = "pk_test_the_ci_job"

# A production key: no corner at all, because what is deployed is the ORG's.
PRODUCTIONS_KEY = "pk_live_the_orgs_own"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The sandbox's instance: where the corners are, and so where a line is."""
    return a_sandbox(settings)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {
            BERNAS_KEY: a_laptop("k_berna", BERNA),
            CARLAS_KEY: a_laptop("k_carla", CARLA),
            CI_KEY: KeyRecord(key_id="k_ci", org=A_RECORD.org, env=SANDBOX, label="ci"),
            PRODUCTIONS_KEY: KeyRecord(key_id="k_prod", org=A_RECORD.org, env=PRODUCTION),
        }
    )


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers(
        [a_member(BERNA, "berna@clinica.test"), a_member(CARLA, "carla@clinica.test")]
    )


async def running(registry: Registry, socket: str, holder: str) -> None:
    """One developer's `pinecall start`, holding the agent and the shared sandbox number."""
    await registry.register(
        socket,
        A_RECORD.org,
        SANDBOX,
        AGENT,
        holder=holder,
    )


@pytest.fixture
async def bernas(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """Berna's terminal, holding her own sandbox key."""
    http = over_the_asgi_app(f"Bearer {BERNAS_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
async def carlas(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """Carla's, holding hers: two corners of one world, which is the whole subject here."""
    http = over_the_asgi_app(f"Bearer {CARLAS_KEY}")
    yield http
    await http.aclose()


async def test_the_first_terminal_to_hold_the_agent_answers_its_ring(
    bernas: httpx.AsyncClient, registry: Registry
) -> None:
    """Alone, nobody claims anything: a single developer never learns the word 'line'."""
    await running(registry, BERNAS_SOCKET, BERNA)

    said = (await bernas.get(LINE)).json()

    assert said["held"] is True
    assert said["yours"] is True
    assert said["holding"] == {"holder": BERNA, "name": "berna@clinica.test"}
    assert said["waiting"] == []


async def test_starting_later_does_not_take_it_and_the_terminal_is_told_whose_it_is(
    carlas: httpx.AsyncClient, registry: Registry
) -> None:
    await running(registry, BERNAS_SOCKET, BERNA)
    await running(registry, CARLAS_SOCKET, CARLA)

    said = (await carlas.get(LINE)).json()

    assert said["yours"] is False
    assert said["holding"]["name"] == "berna@clinica.test"
    assert [one["name"] for one in said["waiting"]] == ["carla@clinica.test"]


async def test_a_claim_takes_it_and_the_other_terminal_sees_it_go(
    bernas: httpx.AsyncClient, carlas: httpx.AsyncClient, registry: Registry
) -> None:
    await running(registry, BERNAS_SOCKET, BERNA)
    await running(registry, CARLAS_SOCKET, CARLA)

    claimed = await carlas.post(LINE)

    assert claimed.status_code == 200
    assert claimed.json()["yours"] is True
    assert (await bernas.get(LINE)).json()["yours"] is False
    taking = registry.taking(SANDBOX, AGENT)
    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_a_claim_on_an_agent_this_terminal_is_not_running_is_refused(
    bernas: httpx.AsyncClient, carlas: httpx.AsyncClient, registry: Registry
) -> None:
    """A ring lands on the line, so a corner with no app in it would take the call and drop it."""
    await running(registry, BERNAS_SOCKET, BERNA)

    refused = await carlas.post(LINE)

    assert refused.status_code == 409
    assert "is not held in sandbox" in refused.json()["detail"]
    assert (await bernas.get(LINE)).json()["yours"] is True


async def test_dropping_it_hands_it_to_whoever_is_still_running(
    bernas: httpx.AsyncClient, registry: Registry
) -> None:
    await running(registry, BERNAS_SOCKET, BERNA)
    await running(registry, CARLAS_SOCKET, CARLA)

    dropped = await bernas.delete(LINE)

    assert dropped.status_code == 200
    assert dropped.json()["holding"]["name"] == "carla@clinica.test"


async def test_nobody_answers_a_ring_at_an_agent_no_terminal_is_running(
    bernas: httpx.AsyncClient,
) -> None:
    said = (await bernas.get(LINE)).json()

    assert said["held"] is False
    assert said["holding"] is None
    assert said["waiting"] == []


# ── whose phone dialled ─────────────────────────────────────────────────────────

A_PHONE = "/v1/line/from"
BERNAS_PHONE = "+59899111111"


async def test_a_developer_says_which_phone_is_theirs_and_the_door_says_it_back(
    bernas: httpx.AsyncClient, registry: Registry
) -> None:
    await running(registry, BERNAS_SOCKET, BERNA)

    said = await bernas.put(A_PHONE, json={"number": BERNAS_PHONE})

    assert said.status_code == 200
    assert said.json()["calling"] == [BERNAS_PHONE]
    assert (await bernas.get(LINE)).json()["calling"] == [BERNAS_PHONE]


async def test_a_number_that_is_not_a_number_is_refused_with_the_shape_in_the_sentence(
    bernas: httpx.AsyncClient, registry: Registry
) -> None:
    await running(registry, BERNAS_SOCKET, BERNA)

    refused = await bernas.put(A_PHONE, json={"number": "099 111 111"})

    assert refused.status_code == 400
    assert "E.164" in refused.json()["detail"]


async def test_an_orgs_own_key_has_no_corner_to_route_a_call_into(wired: None) -> None:  # noqa: ARG001
    """CI's key names nobody, so there is no `their own agent` for a number to reach."""
    ci = over_the_asgi_app(f"Bearer {CI_KEY}")
    try:
        refused = await ci.put(A_PHONE, json={"number": BERNAS_PHONE})
    finally:
        await ci.aclose()

    assert refused.status_code == 403
    assert "names no person" in refused.json()["detail"]


async def test_forgetting_says_which_numbers_were_forgotten(
    bernas: httpx.AsyncClient, registry: Registry
) -> None:
    await running(registry, BERNAS_SOCKET, BERNA)
    await bernas.put(A_PHONE, json={"number": BERNAS_PHONE})

    forgot = await bernas.delete(A_PHONE)

    assert forgot.json()["forgot"] == [BERNAS_PHONE]
    assert (await bernas.get(LINE)).json()["calling"] == []


# ── the numbers a developer's phone dials ───────────────────────────────────────

TO_CALL = "/v1/line/numbers"
THE_REAL_NUMBER = "+14176743169"


# The numbers are production's rows, in production's database: the sandbox asks production for
# them on the fleet key production minted for it, and answers only the phone doors.
async def test_a_developer_reads_the_production_numbers_their_phone_can_dial(
    bernas: httpx.AsyncClient, other_instance: Scripting
) -> None:
    doors = [
        Route(A_RECORD.org, AGENT, "phone", THE_REAL_NUMBER),
        Route(A_RECORD.org, AGENT, "web", None),
    ]
    production = other_instance(
        get_production_peer, httpx.Response(200, json=[*map(asdict, doors)])
    )
    await bernas.put(A_PHONE, json={"number": BERNAS_PHONE})

    said = await bernas.get(TO_CALL)

    assert said.json() == {
        "calling": [BERNAS_PHONE],
        "numbers": [{"number": THE_REAL_NUMBER, "agent": AGENT}],
    }
    (asked,) = production.asked
    assert (asked.url.path, dict(asked.url.params)) == (
        "/v1/routes",
        {"org": A_RECORD.org, "env": PRODUCTION},
    )
    assert asked.headers["Authorization"] == f"Bearer {A_PEER_KEY}"


async def test_a_production_that_does_not_answer_is_said_and_nothing_is_made_up(
    bernas: httpx.AsyncClient, other_instance: Scripting
) -> None:
    other_instance(get_production_peer, httpx.ConnectError("nobody home"))
    assert (await bernas.get(TO_CALL)).status_code == 502


async def test_a_sandbox_that_holds_no_key_of_production_says_which_verb_mints_one(
    bernas: httpx.AsyncClient,
) -> None:
    refused = await bernas.get(TO_CALL)
    assert refused.status_code == 503
    assert "box peer" in refused.json()["detail"]


async def test_a_key_that_names_nobody_has_no_phone_to_dial_from(wired: None) -> None:  # noqa: ARG001
    async with over_the_asgi_app(f"Bearer {CI_KEY}") as ci:
        assert (await ci.get(TO_CALL)).status_code == 403


# PRODUCTION HAS NO CORNERS. `held_by` answers None there for every key and so does the line's
# holder, so `holder == whose` was None == None — true — and `pinecall line` told a laptop holding
# nothing that the number "rings in this terminal", about a box (production, 2026-09-20).
@pytest.fixture
async def the_boxs(wired: None, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """A production key at production's instance: no corner, what is deployed is the ORG's."""
    answering_in(PRODUCTION, settings)
    http = over_the_asgi_app(f"Bearer {PRODUCTIONS_KEY}")
    yield http
    await http.aclose()


async def test_a_production_key_is_never_told_the_line_is_its_own(
    the_boxs: httpx.AsyncClient, registry: Registry
) -> None:
    """What is deployed is the org's: no terminal owns the ring, so none is told it does."""
    await registry.register(
        "app_on_the_box",
        A_RECORD.org,
        PRODUCTION,
        AGENT,
    )

    said = (await the_boxs.get(LINE)).json()

    assert said["held"] is True
    assert said["yours"] is False
