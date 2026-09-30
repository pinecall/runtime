"""Tests for what a request is: the bearer, its world and scope, a reader, a stream."""

from dataclasses import replace

import pytest
from starlette.requests import HTTPConnection, Request

from pinecall.domain.errors import NotAllowed, TooManyRequests
from pinecall.domain.person import HOLDING, Key
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
from pinecall.tenancy.keys import Bearer
from pinecall.tenancy.signin import TRIES
from pinecall.tenancy.tokens import Visit
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, postgres
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
def test_the_sixth_knock_of_a_name_in_a_minute_is_refused_in_the_sentence_given(
    wired: Gateway,
) -> None:
    for _ in range(TRIES):
        check_knock(wired, "203.0.113.7 */ana", "slow down")
    check_knock(wired, "198.51.100.9 */ana", "slow down")
    with pytest.raises(TooManyRequests, match="slow down"):
        check_knock(wired, "203.0.113.7 */ana", "slow down")


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
