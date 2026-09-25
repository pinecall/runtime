"""The three doors of a code: a page is handed one, waits on it, follows the call that keys it."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from pinecall.api import _deps as deps
from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.auth.scopes import a_call_token, a_code_token
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.orgs.codes import CLAIMED, ISSUED, Codes
from pinecall.routes.table import MemoryRoutes
from pinecall.types import PRODUCTION, Route
from pinecall.worker.client import CONTEXT, Gateway
from tests.api.conftest import A_KEY, A_LIVEKIT, A_RECORD, AGENT, over_the_asgi_app
from tests.api.talking import a_context, a_frame, an_app, declared, entry_until
from tests.api.test_worker_doors import declared as registered

pytestmark = pytest.mark.unit

CALL = "call_keyed_a_code"
THE_CLINICS_NUMBER = "+34910000000"
CODES = "/v1/codes"


@pytest.fixture
def codes(wired: None, logs: Logs) -> Codes:  # noqa: ARG001 — `wired` clears the override after
    """The codes table this gateway answers from, empty at the start of every test."""
    table = Codes(logs)
    app.dependency_overrides[deps.the_codes] = lambda: table
    return table


@pytest.fixture
async def page(wired: None) -> Any:  # noqa: ARG001
    """The browser: no key at all, only the code token it was handed."""
    http = over_the_asgi_app("")
    yield http
    await http.aclose()


async def a_phone(routes: MemoryRoutes) -> None:
    """The clinic's agent answers the phone at the clinic's number, in production."""
    await routes.put(Route(A_RECORD.org, AGENT, "phone", THE_CLINICS_NUMBER, env=PRODUCTION))


async def a_code(tenant_http: httpx.AsyncClient) -> dict[str, Any]:
    """What the tenant's server is handed for its page: the code, the number, the code token."""
    answer = await tenant_http.post(CODES, json={"agent": AGENT, "log": "tenant"})
    assert answer.status_code == 201, answer.text
    said: dict[str, Any] = answer.json()
    return said


async def a_phone_call(worker_gateway: Gateway, registry: Registry) -> None:
    """A phone call to the clinic, opened by the worker that answered it."""
    await registered(registry)
    await worker_gateway.opened(a_context(CALL, channel="phone", caller="+34600000000"), AGENT)


async def written(store: MemoryStore, type: str) -> list[dict[str, Any]]:
    """The agent's own log, kept to one type."""
    return [entry.data for entry in await store.agent_since(AGENT, after=0) if entry.type == type]


async def test_a_code_is_issued_with_the_agents_number_and_a_token(
    tenant_http: httpx.AsyncClient, routes: MemoryRoutes, codes: Codes, store: MemoryStore
) -> None:
    await a_phone(routes)
    said = await a_code(tenant_http)
    assert len(said["code"]) == 4 and said["number"] == THE_CLINICS_NUMBER
    assert said["expires_at"] == pytest.approx(time.time() + 600, abs=5)
    token = a_call_token(said["code_token"], A_LIVEKIT)
    assert token is not None and token.call == "" and token.scope == "read"
    assert (token.code, token.agent, token.env) == (said["code"], AGENT, PRODUCTION)
    assert [one["code"] for one in await written(store, ISSUED)] == [said["code"]]
    assert await codes.standing(PRODUCTION, AGENT, said["code"]) is not None


async def test_no_phone_number_is_a_409(tenant_http: httpx.AsyncClient, codes: Codes) -> None:  # noqa: ARG001
    answer = await tenant_http.post(CODES, json={"agent": AGENT})
    assert answer.status_code == 409
    assert answer.json()["detail"] == (
        f"agent {AGENT} answers at no phone number in production: pinecall numbers import"
    )


async def test_the_page_waits_and_is_answered_the_moment_the_call_claims_it(
    tenant_http: httpx.AsyncClient,
    page: httpx.AsyncClient,
    worker_gateway: Gateway,
    registry: Registry,
    routes: MemoryRoutes,
    codes: Codes,  # noqa: ARG001
    store: MemoryStore,
) -> None:
    await a_phone(routes)
    said = await a_code(tenant_http)
    await a_phone_call(worker_gateway, registry)
    asked = f"{CODES}/{said['code']}?wait=1&token={said['code_token']}"
    waiting = asyncio.ensure_future(page.get(asked))
    await asyncio.sleep(0.05)
    assert not waiting.done()

    assert await worker_gateway.claim(CALL, said["code"]) is True
    answer = await asyncio.wait_for(waiting, 2.0)
    standing = answer.json()
    assert (standing["status"], standing["call"]) == ("claimed", CALL)
    reads = a_call_token(standing["log_token"], A_LIVEKIT)
    assert reads is not None and (reads.call, reads.projection) == (CALL, "tenant")
    claimed = [entry.data for entry in await store.since(CALL) if entry.type == "call.claimed"]
    assert claimed == [{"code": said["code"], "via": "keypad"}]
    assert await written(store, CLAIMED) == [{"code": said["code"], "call": CALL}]


async def test_a_code_token_reads_nothing_but_its_code(
    tenant_http: httpx.AsyncClient,
    page: httpx.AsyncClient,
    worker_gateway: Gateway,
    registry: Registry,
    routes: MemoryRoutes,
    codes: Codes,  # noqa: ARG001
) -> None:
    await a_phone(routes)
    said = await a_code(tenant_http)
    await a_phone_call(worker_gateway, registry)
    token = f"token={said['code_token']}"
    other = "0000" if said["code"] != "0000" else "0001"
    assert (await page.get(f"{CODES}/{said['code']}?{token}")).json()["status"] == "waiting"
    assert (await page.get(f"{CODES}/{other}?{token}")).status_code == 403
    assert (await page.get(f"/v1/calls/{CALL}/events?{token}")).status_code == 403
    assert (await page.get(f"/v1/calls/{CALL}/state?{token}")).status_code == 403
    assert (await page.get(f"/v1/agents/{AGENT}/calls?{token}")).status_code == 403


async def test_a_claim_nobody_issued_is_404(
    tenant_http: httpx.AsyncClient,
    worker_gateway: Gateway,
    registry: Registry,
    routes: MemoryRoutes,
    codes: Codes,  # noqa: ARG001
    store: MemoryStore,
) -> None:
    await a_phone(routes)
    said = await a_code(tenant_http)
    await a_phone_call(worker_gateway, registry)
    nobodys = "0000" if said["code"] != "0000" else "0001"
    assert await worker_gateway.claim(CALL, nobodys) is False
    answer = await tenant_http.post(f"/v1/calls/{CALL}/claim", json={"code": nobodys})
    assert answer.status_code == 404
    assert [entry.type for entry in await store.since(CALL)] == ["call.ringing"]


async def test_an_expired_code_says_so(page: httpx.AsyncClient, codes: Codes) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, 0.05, "public")
    token = a_code_token(issued.code, AGENT, PRODUCTION, time.time() + 60, A_LIVEKIT)
    answer = await page.get(f"{CODES}/{issued.code}?wait=1&token={token}")
    assert answer.status_code == 200
    assert (answer.json()["status"], answer.json()["log_token"]) == ("expired", None)


def test_the_agent_claims_from_the_socket_with_via_agent(
    gateway: TestClient, codes: Codes, store: MemoryStore
) -> None:
    """The class heard the code said: the call is bound once, and a second claim is `no_code`."""
    issued = asyncio.run(codes.issue(PRODUCTION, AGENT, 600, "public"))
    headers = {"Authorization": f"Bearer {A_KEY}"}
    with an_app(gateway) as app_socket:
        declared(app_socket)
        opening = CONTEXT.dump_python(a_context(CALL, channel="phone"), mode="json")
        handle: Any = gateway
        handle.post("/v1/calls", json={"agent": AGENT, "context": opening}, headers=headers)
        app_socket.send_json(a_frame("call.claim", AGENT, {"code": issued.code}, call=CALL))
        entry_until(app_socket, "call.claimed")
        app_socket.send_json(a_frame("call.claim", AGENT, {"code": issued.code}, call=CALL))
        refused = entry_until(app_socket, "error")
        assert refused["data"]["code"] == "no_code"
    claimed = asyncio.run(store.since(CALL))
    assert [entry.data for entry in claimed if entry.type == "call.claimed"] == [
        {"code": issued.code, "via": "agent"}
    ]
