"""Every tenant door asks the key for ONE scope and refuses in one sentence; the test names them."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.api._deps import SCOPE_OF_THE_DOOR
from pinecall.api.app import app
from pinecall.auth.bearer import POLICY_VIOLATION
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.types import KEY_SCOPES
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS, CHAT
from tests.api.talking import a_door, a_frame, a_register, got

pytestmark = pytest.mark.unit

# Two people of the clinic with narrow keys: one who only reads, one who only holds the agent.
A_READER_KEY = "pk_test_qa_reads_calls"
A_READER = KeyRecord(
    key_id="k_qa",
    org=A_RECORD.org,
    scopes=frozenset({"calls", "evals"}),
    subject="m_qa",
    name="Ana",
)
AN_APP_KEY = "pk_test_the_apps_own"
AN_APP = KeyRecord(key_id="k_app", org=A_RECORD.org, scopes=frozenset({"app"}))


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_READER_KEY: A_READER, AN_APP_KEY: AN_APP})


def refused_with(status: int, body: dict[str, Any], scope: str, record: KeyRecord) -> None:
    assert status == 403
    assert body["detail"] == NOT_OPENED.format(scope=scope, opens=" · ".join(sorted(record.scopes)))


def test_a_key_that_only_reads_is_refused_the_knowledge_base_and_told_what_it_opens(
    gateway: TestClient,
) -> None:
    status, body = got(gateway, "/v1/knowledge", A_READER_KEY)
    refused_with(status, body, "knowledge", A_READER)
    status, _ = got(gateway, "/v1/agents", A_READER_KEY)
    assert status == 200, "and the door its scope opens is open"


def test_whoami_takes_any_key_whatever_it_opens(gateway: TestClient) -> None:
    status, body = got(gateway, "/v1/whoami", AN_APP_KEY)
    assert status == 200 and body["scopes"] == ["app"]


def test_the_read_doors_ask_a_key_for_calls(gateway: TestClient) -> None:
    """One rule at the reader every log door shares: the app's own key reads no session list."""
    status, body = got(gateway, f"/v1/agents/{AGENT}/sessions", AN_APP_KEY)
    refused_with(status, body, "calls", AN_APP)


def test_the_verbs_door_asks_a_key_for_supervise(gateway: TestClient) -> None:
    handle: Any = gateway
    answer: Any = handle.post(
        "/v1/calls/call_x/verbs",
        json={"verb": "end", "reason": "test"},
        headers={"Authorization": f"Bearer {A_READER_KEY}"},
    )
    refused_with(answer.status_code, answer.json(), "supervise", A_READER)


def test_the_app_socket_closes_a_key_without_app_and_says_why(gateway: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        with gateway.websocket_connect(
            APPS, headers={"Authorization": f"Bearer {A_READER_KEY}"}
        ) as socket:
            socket.send_json(a_register(AGENT, a_door("web")))
            socket.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert refused.value.reason == NOT_OPENED.format(scope="app", opens="calls · evals")


def test_the_chat_socket_closes_a_key_without_talk_and_says_why(gateway: TestClient) -> None:
    with gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {AN_APP_KEY}"}) as held:
        held.send_json(a_register(AGENT, a_door("web")))
        held.receive_json()
        with pytest.raises(WebSocketDisconnect) as refused:
            with gateway.websocket_connect(
                f"{CHAT}?agent={AGENT}", headers={"Authorization": f"Bearer {AN_APP_KEY}"}
            ) as caller:
                caller.send_json(a_frame("ping", AGENT))
                caller.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert refused.value.reason == NOT_OPENED.format(scope="talk", opens="app")


# ── the rule, pinned over the app itself ────────────────────────────────────────

# The doors that take a key and ask it for no scope, or ask inside rather than by a dep, each with
# the reason. Anything else under /v1 that is not the operator's must declare exactly one scope.
ASKS_NOTHING_OR_ASKS_INSIDE: dict[str, str] = {
    "GET /v1/whoami": "any key may ask whose it is",
    "POST /v1/login": "takes no key: it mints one",
    "POST /v1/signup": "takes no key: it makes the org that will hold one, on the cloud alone",
    "POST /v1/login/codes": "any key may mint a code for its own record",
    "POST /v1/login/env": "a person's key may mint the same person's key in the other world",
    "POST /v1/login/pairings": "takes no key: a terminal asking to be signed in has none yet",
    "GET /v1/login/pairings/{code}": "takes no key: what the card approves, no key in it",
    "POST /v1/login/pairings/{code}": "any person's key may sign a terminal in as themselves",
    "GET /v1/login/pairings/{code}/key": "takes no key: the word itself is the right",
    "POST /v1/invitations/{token}": "takes no key: the token is the right",
    "GET /v1/whatsapp/webhook": "Meta's handshake, signed",
    "POST /v1/whatsapp/webhook": "Meta's delivery, signed",
    "POST /v1/calls/{call}/verbs": "a key or a supervise token: the reader is asked inside",
    "WS /v1/apps": "asked inside, so the socket can say why it closed",
    "WS /v1/chat": "asked inside, so the socket can say why it closed",
    "WS /v1/attach": "a key or a supervise token: the reader is asked inside",
}


# The doors that open to EITHER of two scopes, each with the reason. A declaration is read by the
# worker holding the agent and by a console watching it, and a person's key holds no `app` in
# production. Named by path, so a second such door is a line here and never a surprise.
OPENS_TO_EITHER: dict[str, frozenset[str]] = {
    "GET /v1/agents/{slug}/config": frozenset({"app", "calls"}),
}


# FastAPI 0.141 stopped flattening an included router into `app.routes`: it puts a wrapper there
# that holds the real router. Walking `app.routes` alone found no APIRoute at all, so this test
# passed over an empty list and pinned NOTHING — every door's scope went unchecked from the
# upgrade until somebody read the walk. Both shapes are unwrapped here, so it cannot happen again
# quietly: `test_the_walk_actually_reaches_the_doors` below fails if the list comes back empty.
def _doors_of(app_or_router: Any) -> list[Any]:
    """Every real route under an app, through whatever wrapper the version puts in the way."""
    found: list[Any] = []
    for route in app_or_router.routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found.extend(_doors_of(inner))
        else:
            found.append(route)
    return found


def test_the_walk_actually_reaches_the_doors() -> None:
    """The guard on the guard: a flattener that returns nothing makes the test below vacuous."""
    reached = [route for route in _doors_of(app) if isinstance(route, APIRoute)]
    assert len(reached) > 50, f"the walk found {len(reached)} doors, so it is pinning nothing"
    assert any(route.path == "/v1/whoami" for route in reached)


def test_every_tenant_door_declares_exactly_one_scope() -> None:
    """Walk the app: one scoped dep per door; the exceptions are the lists above, never a guess."""
    undeclared: list[str] = []
    for route in _doors_of(app):
        if isinstance(route, APIWebSocketRoute):
            doors = [f"WS {route.path}"]
        elif isinstance(route, APIRoute):
            doors = [f"{method} {route.path}" for method in sorted(route.methods or ())]
        else:
            continue
        if not route.path.startswith("/v1") or route.path.startswith("/v1/ops"):
            continue
        scopes = _scopes_of(route.dependant)
        for door in doors:
            if door in ASKS_NOTHING_OR_ASKS_INSIDE:
                assert not scopes, f"{door} is listed as asking nothing by a dep, and asks {scopes}"
                continue
            wanted = OPENS_TO_EITHER.get(door)
            if len(scopes) != 1 or not scopes[0] <= KEY_SCOPES:
                undeclared.append(f"{door}: {scopes}")
            elif scopes[0] != (wanted or _the_one(scopes[0])):
                undeclared.append(
                    f"{door}: opens to {sorted(scopes[0])}, and is not listed as such"
                )
    assert not undeclared, "\n".join(undeclared)


def _the_one(scopes: frozenset[str]) -> frozenset[str]:
    """This set when it is one scope, or an empty one — which no door declares — when it is not."""
    return scopes if len(scopes) == 1 else frozenset()


def _scopes_of(dependant: Dependant) -> list[frozenset[str]]:
    """The scope sets the door's dependency tree declares, one per scoped dep, wherever they sit."""
    found: list[frozenset[str]] = []
    for dependency in dependant.dependencies:
        scopes = getattr(dependency.call, "__dict__", {}).get(SCOPE_OF_THE_DOOR)
        if scopes is not None:
            found.append(frozenset(scopes))
        found.extend(_scopes_of(dependency))
    return found
