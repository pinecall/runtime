"""The four doors onto the routes over the real ASGI app: who may open them, and what changes."""

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.types import PRODUCTION
from pinecall.worker import router
from pinecall.worker.client import Gateway
from tests.api.conftest import A_KEY, A_RECORD

pytestmark = pytest.mark.unit

OPS_ROUTES = "/v1/ops/routes"
NUMBER = "+59829000000"
AN_OWNER = "app_the_doors"  # the id an app socket would have been minted
CLINICA = "clinica-norte"
TIENDA = "tienda-sur"


async def holding(registry: Registry, agent: str = CLINICA) -> None:
    """The agent's app on its socket. It claims no door: a door is a row an operator typed."""
    await registry.register(AN_OWNER, A_RECORD.org, PRODUCTION, agent)


async def typed(ops_http: httpx.AsyncClient, agent: str, number: str = NUMBER) -> None:
    """One door, the only way there is to make one: an operator types it."""
    answer = await ops_http.post(
        OPS_ROUTES,
        json={"org": A_RECORD.org, "number": number, "agent": agent, "channel": "phone"},
    )
    assert answer.status_code == httpx.codes.OK


def arrived(number: str = NUMBER) -> router.Arrival:
    """A call at that number, as worker/router.py reads one off a job's SIP attributes."""
    return router.Arrival(
        caller="+59899111222", channel="phone", direction="inbound", number=number
    )


async def test_a_route_added_while_a_worker_runs_answers_the_next_job_with_nobody_restarted(
    worker_gateway: Gateway, ops_http: httpx.AsyncClient, registry: Registry
) -> None:
    """Criterion 1: the same worker, the same process, two jobs, two different agents."""
    await holding(registry)
    await typed(ops_http, CLINICA)
    first = router.resolve(arrived(), await worker_gateway.routes())
    assert first.agent == CLINICA

    await typed(ops_http, TIENDA)

    second = router.resolve(arrived(), await worker_gateway.routes())
    assert second.agent == TIENDA


async def test_the_worker_reads_every_row_of_its_corner_and_no_widget(
    worker_gateway: Gateway, ops_http: httpx.AsyncClient, registry: Registry
) -> None:
    """One org, two rows, and the agent that answers only on the web has nothing here to read."""
    await holding(registry)
    await holding(registry, TIENDA)
    await typed(ops_http, CLINICA)
    await typed(ops_http, TIENDA, "+59829000009")

    answered = await worker_gateway.routes()

    assert {(route.agent, route.channel) for route in answered} == {
        (CLINICA, "phone"),
        (TIENDA, "phone"),
    }


async def test_the_operator_is_shown_every_row_of_the_org(
    ops_http: httpx.AsyncClient, registry: Registry
) -> None:
    """`routes list` is this door: the same doors the worker gets, and there is one table now."""
    await holding(registry)
    await typed(ops_http, TIENDA, "+59829000009")

    listed = await ops_http.get(OPS_ROUTES, params={"org": A_RECORD.org})

    assert [(route["agent"], route["number"]) for route in listed.json()] == [
        (TIENDA, "+59829000009")
    ]


async def test_removing_a_route_leaves_the_number_answering_nowhere(
    worker_gateway: Gateway, ops_http: httpx.AsyncClient, registry: Registry
) -> None:
    """A row was the only thing answering it, so a number taken out rings nowhere at all."""
    await holding(registry)
    await typed(ops_http, CLINICA)

    removed = await ops_http.delete(f"{OPS_ROUTES}/{NUMBER}", params={"org": A_RECORD.org})

    assert removed.status_code == httpx.codes.NO_CONTENT
    with pytest.raises(router.NoRoute):
        router.resolve(arrived(), await worker_gateway.routes())


async def test_removing_a_number_nobody_typed_is_a_refusal_and_never_a_quiet_success(
    ops_http: httpx.AsyncClient,
) -> None:
    """A typo in `routes rm` must not read as done."""
    answer = await ops_http.delete(f"{OPS_ROUTES}/+59800000000", params={"org": A_RECORD.org})
    assert answer.status_code == httpx.codes.NOT_FOUND


async def test_a_number_that_is_not_a_number_is_refused_in_the_domains_own_words(
    ops_http: httpx.AsyncClient,
) -> None:
    """The Route refuses it before the table sees it, and the operator reads why."""
    answer = await ops_http.post(
        OPS_ROUTES,
        json={"org": A_RECORD.org, "number": "29000000", "agent": TIENDA, "channel": "phone"},
    )
    assert answer.status_code == httpx.codes.BAD_REQUEST
    assert "E.164" in answer.json()["detail"]


@pytest.mark.parametrize("authorization", ["", f"Bearer {A_KEY}", "Bearer nope"])
async def test_the_operator_doors_take_the_ops_key_and_no_other(
    ops_http: httpx.AsyncClient, authorization: str
) -> None:
    """A org's own API key opens the worker's read and not these: they are the box's."""
    headers = {"Authorization": authorization} if authorization else {"Authorization": ""}
    answer = await ops_http.get(OPS_ROUTES, params={"org": A_RECORD.org}, headers=headers)
    assert answer.status_code == httpx.codes.UNAUTHORIZED


async def test_the_workers_read_takes_the_fleets_key_and_not_the_operators(
    ops_http: httpx.AsyncClient,
) -> None:
    """/v1/routes is the worker's door: the ops key is not an API key, and it is told nothing."""
    answer = await ops_http.get("/v1/routes")
    assert answer.status_code == httpx.codes.UNAUTHORIZED
