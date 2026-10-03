"""Tests for what a request is: the bearer, its world and scope, a reader, a stream."""

from dataclasses import replace

import pytest
from starlette.requests import HTTPConnection, Request

from pinecall.domain.errors import NotAllowed, TooManyRequests
from pinecall.domain.person import HOLDING, KEY_SCOPES, Key
from pinecall.domain.scope import Scope
from pinecall.gateway._deps import (
    SCOPES_OF,
    Reader,
    bearer_of,
    check_knock,
    client_of,
    dispatched,
    opening,
    public_url,
    world_of_request,
)
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy import orgs, people, throttle
from pinecall.tenancy.keys import Bearer
from pinecall.tenancy.signin import TRIES
from pinecall.tenancy.tokens import Visit
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, a_developer, issued, postgres
from tests.gateway.api.conftest import a_call, an_app, bound_to


def a_request(
    client: tuple[str, int] | None = ("203.0.113.7", 5060), host: str = "gateway.test"
) -> Request:
    """A request to the gateway at the host given, from the client given."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("gateway.test", 80),
            "path": "/",
            "headers": [(b"host", host.encode())],
            "client": client,
        }
    )


def test_the_bearer_is_read_from_the_header_and_nothing_else_is_one() -> None:
    assert bearer_of({"authorization": "Bearer pc_test_1"}) == "pc_test_1"
    assert bearer_of({"authorization": "Basic abc"}) is None
    assert bearer_of({}) is None


def test_each_scope_a_door_opens_is_recorded_for_the_walk() -> None:
    opened = opening("app", "fleet")
    assert SCOPES_OF[opened] == frozenset({"app", "fleet"})


def test_only_a_dispatch_that_names_org_and_world_is_a_corner() -> None:
    assert dispatched("org_a", "sandbox", "m_1") == Scope("org_a", "sandbox", "m_1")
    assert dispatched("org_a", None, None) is None


def test_a_key_reads_the_tenants_and_a_token_its_grants() -> None:
    assert Reader().projection == "tenant"
    visit = Visit(call="call_1", scope="talk", expires_at=0.0)
    assert Reader(visit=visit).projection == "public"
    read = Visit(call="call_1", scope="read", expires_at=0.0, projection="tenant")
    assert Reader(visit=read).projection == "tenant"


def test_the_client_is_the_address_uvicorn_read_and_unknown_without_one() -> None:
    assert client_of(a_request()) == "203.0.113.7"
    assert client_of(a_request(None)) == "unknown"


@postgres
async def test_the_sixth_knock_of_a_name_in_a_minute_is_refused_in_the_sentence_given(
    wired: Gateway,
) -> None:
    for _ in range(TRIES):
        await check_knock(wired, "203.0.113.7 */ana", "slow down")
    await check_knock(wired, "198.51.100.9 */ana", "slow down")
    with pytest.raises(TooManyRequests, match="slow down"):
        await check_knock(wired, "203.0.113.7 */ana", "slow down")


@postgres
def test_the_public_url_is_the_boxs_name_and_the_requests_only_where_it_has_none(
    wired: Gateway,
) -> None:
    assert public_url(a_request(), wired) == "https://box.test"
    nameless = wired.connections.settings.model_copy(update={"domain": None})
    unnamed = replace(wired, connections=replace(wired.connections, settings=nameless))
    assert public_url(a_request(), unnamed) == "http://gateway.test"


@postgres
def test_the_public_url_is_the_sandboxs_name_for_a_request_that_came_in_by_it(
    wired: Gateway,
) -> None:
    named = wired.connections.settings.model_copy(update={"sandbox_domain": "sandbox.box.test"})
    both = replace(wired, connections=replace(wired.connections, settings=named))
    assert public_url(a_request(host="sandbox.box.test"), both) == "https://sandbox.box.test"
    assert public_url(a_request(host="box.test"), both) == "https://box.test"
    assert public_url(a_request(host="forged.test"), both) == "https://box.test"


@postgres
def test_a_socket_at_the_sandboxs_name_acts_in_the_sandbox_and_may_not_ask_for_production(
    wired: Gateway,
) -> None:
    named = wired.connections.settings.model_copy(update={"sandbox_domain": "sandbox.box.test"})
    both = replace(wired, connections=replace(wired.connections, settings=named))
    server = Bearer(Key("k_1", "org_1", env="sandbox", scopes=frozenset({HOLDING})))

    def socket(*headers: tuple[bytes, bytes]) -> HTTPConnection:
        return HTTPConnection(
            {
                "type": "websocket",
                "path": "/v1/chat",
                "headers": [(b"host", b"sandbox.box.test"), *headers],
            }
        )

    assert world_of_request(socket(), server, both) == "sandbox"
    with pytest.raises(NotAllowed, match="this name is the sandbox's"):
        world_of_request(socket((b"pinecall-env", b"production")), server, both)


# The path's agent, the query's, and a call's by its head: each refused past the member's list.
@postgres
async def test_a_member_bound_to_other_agents_is_refused_the_ones_a_door_names(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    elsewhere = await bound_to(knocking, "bo@clinica.test", frozenset({"ventas"}))
    everyone = await bound_to(knocking, "cy@clinica.test", frozenset())
    ours = await bound_to(knocking, "di@clinica.test", frozenset({AGENT}))
    paths = (
        f"/v1/agents/{AGENT}/settings",
        f"/v1/sessions?agent={AGENT}",
        f"/v1/calls/{context.call}/state",
    )
    answers: dict[str, list[int]] = {}
    for name, key in (("elsewhere", elsewhere), ("everyone", everyone), ("ours", ours)):
        async with knocking.http(key) as person:
            answers[name] = [(await person.get(path)).status_code for path in paths]
    assert answers == {"elsewhere": [403] * 3, "everyone": [200] * 3, "ours": [200] * 3}
    await socket.close()


# One count per org, world and family; the platform's own keys and an operator are never paced.
@postgres
async def test_an_org_past_its_minute_is_told_when_to_come_back_and_nobody_else_waits(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(throttle, "REQUESTS_A_MINUTE", 2)
    stranger = await orgs.create(knocking.gateway.connections.pool, "otra", "Otra")
    theirs = await issued(knocking.gateway.connections.pool, stranger.id, "sandbox", KEY_SCOPES)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        let_in = [(await tenant.get("/v1/sessions")).status_code for _ in range(2)]
        paced = await tenant.get("/v1/sessions")
        other_family = await tenant.get("/v1/knowledge")
    async with knocking.http(knocking.app["production"]) as production_key:
        production = await production_key.get("/v1/sessions")
    async with knocking.http(theirs) as other:
        other_org = await other.get("/v1/sessions")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        beat = {
            "fleet": "pinecall-sandbox",
            "worker": "w1",
            "active": 0,
            "max_jobs": 4,
            "load": 0.0,
            "draining": False,
        }
        beats = [(await worker.post("/v1/fleet/heartbeat", json=beat)) for _ in range(3)]
    assert let_in == [200, 200]
    assert paced.status_code == 429
    assert 1 <= int(paced.headers["retry-after"]) <= 60
    assert (
        "this org sent its calls doors 2 requests this minute, in sandbox"
        in (paced.json()["detail"])
    )
    assert (other_family.status_code, other_org.status_code, production.status_code) == (
        200,
        200,
        200,
    )
    assert [beat.status_code for beat in beats] == [200, 200, 200]


@postgres
async def test_an_operator_is_never_paced(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(throttle, "REQUESTS_A_MINUTE", 1)
    member, key = await a_developer(knocking, "op@box.test")
    await people.make_operator(knocking.gateway.connections.pool, knocking.org.id, member, on=True)
    async with knocking.http(key) as operator:
        answers = [(await operator.get("/v1/sessions")).status_code for _ in range(3)]
    assert answers == [200, 200, 200]
