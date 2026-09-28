"""Tests for what a request is: the bearer, its world and scope, a reader, a stream."""

from dataclasses import replace

import pytest
from starlette.requests import Request

from pinecall.domain.errors import TooManyRequests
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
)
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy.signin import TRIES
from pinecall.tenancy.tokens import Visit
from tests.conftest import postgres


def a_request(client: tuple[str, int] | None = ("203.0.113.7", 5060)) -> Request:
    """A request to the gateway at gateway.test, from the client given."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("gateway.test", 80),
            "path": "/",
            "headers": [(b"host", b"gateway.test")],
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
