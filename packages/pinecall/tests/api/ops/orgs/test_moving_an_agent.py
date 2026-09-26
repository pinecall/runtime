"""`orgs move`: the one thing that undoes a slug landing in the wrong org on a first install."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.ops.orgs import NO_SUCH_AGENT, NOT_HELD
from pinecall.live.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.types import PRODUCTION, Route
from tests.api.conftest import A_RECORD, AGENT, AN_ORG
from tests.api.ops.orgs.test_two_orgs_never_cross import ANOTHER_ORG

pytestmark = pytest.mark.unit

MOVE = f"/v1/ops/orgs/{ANOTHER_ORG.slug}/agents"
AN_OWNER = "app_the-clinicas-laptop"


@pytest.fixture
def orgs() -> MemoryOrgs:
    """Two tenants: the one the agent landed in by mistake, and the one it belongs to."""
    return MemoryOrgs([AN_ORG, ANOTHER_ORG])


async def held(registry: Registry) -> None:
    """The agent's app, holding it, exactly as its socket would have claimed it."""
    await registry.register(AN_OWNER, A_RECORD.org, PRODUCTION, AGENT)


async def written(store: MemoryStore, *calls: str) -> None:
    """The agent's own log and one head row per call it took, all the first org's."""
    await store.owned(None, AGENT, AN_ORG.id)
    for call in calls:
        await store.owned(call, AGENT, AN_ORG.id)


# A slug is one org's for as long as its log is, so the wrong first `run` used to be permanent.
# Every call it ever took moves with it: the head row of each carries the owner, and a call left
# behind would be a call the org that now holds the agent cannot read.
async def test_an_agent_and_every_call_of_it_land_in_the_other_org(
    ops_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    await written(store, "call_one", "call_two")

    moved = await ops_http.put(MOVE, json={"agent": AGENT})

    assert moved.status_code == 200, moved.text
    assert moved.json() == {
        "agent": AGENT,
        "org": ANOTHER_ORG.slug,
        "logs": 3,
        "numbers": [],
        "stayed": [],
    }
    assert await store.owner(None, AGENT) == ANOTHER_ORG.id
    assert await store.owner("call_one", AGENT) == ANOTHER_ORG.id
    assert await store.owner("call_two", AGENT) == ANOTHER_ORG.id


async def test_an_agent_somebody_is_holding_right_now_is_refused(
    ops_http: httpx.AsyncClient, store: MemoryStore, registry: Registry
) -> None:
    """The socket holding it believes what it registered with; moving under it splits the two."""
    await written(store)
    await held(registry)

    refused = await ops_http.put(MOVE, json={"agent": AGENT})

    assert refused.status_code == 409
    assert refused.json()["detail"] == NOT_HELD.format(slug=AGENT)
    assert await store.owner(None, AGENT) == AN_ORG.id, "and nothing moved"


async def test_it_is_allowed_again_the_moment_the_terminal_lets_go(
    ops_http: httpx.AsyncClient, store: MemoryStore, registry: Registry
) -> None:
    await written(store)
    await held(registry)
    await registry.release(AN_OWNER)

    assert (await ops_http.put(MOVE, json={"agent": AGENT})).status_code == 200
    assert await store.owner(None, AGENT) == ANOTHER_ORG.id


async def test_an_agent_nobody_has_ever_run_is_a_404_and_not_a_quiet_yes(
    ops_http: httpx.AsyncClient,
) -> None:
    refused = await ops_http.put(MOVE, json={"agent": "nobody-ran-this"})
    assert refused.status_code == 404
    assert refused.json()["detail"] == NO_SUCH_AGENT.format(slug="nobody-ran-this")


async def test_the_door_is_the_boxs_and_not_a_tenants(tenant_http: httpx.AsyncClient) -> None:
    """An org that could pull a slug would be an org that could take another's agent."""
    assert (await tenant_http.put(MOVE, json={"agent": AGENT})).status_code in (401, 403, 404)


# The doors go with it. Left behind, the number kept answering for an org that no longer holds the
# slug — a number that reaches nobody, and nothing said so until somebody called it. Found on the
# box: `clinica-norte` had moved to `pinecall` and its `+1417…` was still `default`'s.
A_NUMBER = "+14176743169"
ANOTHER_NUMBER = "+34910000000"


async def test_the_agents_numbers_move_with_it(
    ops_http: httpx.AsyncClient, store: MemoryStore, routes: MemoryRoutes
) -> None:
    await written(store)
    await routes.put(Route(org=AN_ORG.id, agent=AGENT, channel="phone", number=A_NUMBER))

    moved = await ops_http.put(MOVE, json={"agent": AGENT})

    assert moved.json()["numbers"] == [A_NUMBER]
    assert await routes.of_org(AN_ORG.id, PRODUCTION) == ()
    landed = await routes.of_org(ANOTHER_ORG.id, PRODUCTION)
    assert [route.number for route in landed] == [A_NUMBER]


async def test_a_number_the_other_org_already_answers_at_stays_and_is_named(
    ops_http: httpx.AsyncClient, store: MemoryStore, routes: MemoryRoutes
) -> None:
    """Two orgs typing one number is a thing the schema allows; which row answers is not this
    verb's to decide, so it says what it left rather than choosing."""
    await written(store)
    await routes.put(Route(org=AN_ORG.id, agent=AGENT, channel="phone", number=A_NUMBER))
    await routes.put(Route(org=AN_ORG.id, agent=AGENT, channel="phone", number=ANOTHER_NUMBER))
    await routes.put(Route(org=ANOTHER_ORG.id, agent="otra", channel="phone", number=A_NUMBER))

    moved = (await ops_http.put(MOVE, json={"agent": AGENT})).json()

    assert (moved["numbers"], moved["stayed"]) == ([ANOTHER_NUMBER], [A_NUMBER])
    theirs = await routes.of_org(ANOTHER_ORG.id, PRODUCTION)
    assert sorted(str(route.agent) for route in theirs) == [AGENT, "otra"]
