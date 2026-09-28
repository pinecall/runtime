"""Tests for what a request is: the bearer, its world and scope, a reader, a stream."""

from pinecall.domain.scope import Scope
from pinecall.gateway._deps import SCOPES_OF, Reader, bearer_of, dispatched, opening
from pinecall.tenancy.tokens import Visit


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
