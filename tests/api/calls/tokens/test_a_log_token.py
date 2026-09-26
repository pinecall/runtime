"""The log token a mint answers with: one call's log, through the projection asked, and no more."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.types.token import PROJECTION_ATTRIBUTE, SCOPE_ATTRIBUTE
from tests.api.calls.tokens.test_the_door import minted, payload_of
from tests.api.conftest import AGENT
from tests.api.talking import a_door, a_register, an_app

pytestmark = pytest.mark.unit


def a_log_token(gateway: TestClient, projection: str | None = None) -> tuple[str, str]:
    """A visit minted for the clinic, and the call and log token it answered with."""
    body: dict[str, Any] = {"agent": AGENT}
    if projection is not None:
        body["log"] = projection
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, said = minted(gateway, body)
    assert status == 201, said
    return str(said["call"]), str(said["log_token"])


def read(gateway: TestClient, path: str, token: str) -> int:
    """One read at a door, the token on the query string as a browser's EventSource sends it."""
    handle: Any = gateway
    answer: Any = handle.get(path, params={"token": token})
    status: int = answer.status_code
    return status


def test_the_log_token_reads_through_the_public_projection_unless_the_tenant_asks(
    gateway: TestClient,
) -> None:
    _, public = a_log_token(gateway)
    _, tenant = a_log_token(gateway, "tenant")
    assert payload_of(public)["attributes"][PROJECTION_ATTRIBUTE] == "public"
    assert payload_of(tenant)["attributes"][PROJECTION_ATTRIBUTE] == "tenant"
    assert payload_of(tenant)["attributes"][SCOPE_ATTRIBUTE] == "read"


def test_the_log_token_reads_its_own_call_and_no_other(gateway: TestClient) -> None:
    call, token = a_log_token(gateway, "tenant")
    assert read(gateway, f"/v1/calls/{call}/events", token) == 200
    assert read(gateway, "/v1/calls/call_somebody_elses/events", token) == 403
    assert read(gateway, f"/v1/agents/{AGENT}/calls", token) == 403


def test_the_log_token_sends_no_verb(gateway: TestClient) -> None:
    call, token = a_log_token(gateway, "tenant")
    handle: Any = gateway
    answer: Any = handle.post(
        f"/v1/calls/{call}/verbs",
        json={"verb": "end"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert answer.status_code == 403
