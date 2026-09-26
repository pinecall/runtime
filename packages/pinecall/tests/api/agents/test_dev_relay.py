"""The console asks the gateway, the gateway asks the app in the directory, and the answer comes."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from pinecall.api.agents import dev
from pinecall.live.calls import Live
from pinecall.live.registry import Registry
from pinecall.log.entry import Entry
from pinecall.types import PRODUCTION
from pinecall_protocol.commands import DevAnswer, DevRefusal
from tests.api.conftest import A_RECORD, AGENT

pytestmark = pytest.mark.unit

AN_OWNER = "app_in_the_directory"
DEV = f"/v1/agents/{AGENT}/dev"
# How long a test waits for an entry the door is about to send: a bound on a hang, never a clock
# the test's outcome depends on.
SOCKET_WAITS_S = 5.0


class _AnAppSocket:
    """The app's side of the socket, as Live hands entries to it: kept, and handed on in order."""

    def __init__(self) -> None:
        self.heard: list[Entry] = []
        self._coming: asyncio.Queue[Entry] = asyncio.Queue()

    async def send(self, entry: Entry) -> None:
        self.heard.append(entry)
        self._coming.put_nowait(entry)

    async def next_heard(self) -> Entry:
        """The next entry this socket is handed, the moment it is: no clock between the two."""
        return await asyncio.wait_for(self._coming.get(), timeout=SOCKET_WAITS_S)


async def holding(registry: Registry, live: Live, takes_unclaimed: bool = True) -> _AnAppSocket:
    """The app holding the clinic on one socket, connected to this process's live memory."""
    await registry.register(
        AN_OWNER,
        A_RECORD.org,
        PRODUCTION,
        AGENT,
        takes_unclaimed=takes_unclaimed,
    )
    socket = _AnAppSocket()
    live.connect(AN_OWNER, socket.send)
    return socket


async def the_request(socket: _AnAppSocket) -> Entry:
    """The dev.request the app heard, once it has."""
    return await socket.next_heard()


async def test_the_ask_travels_down_the_apps_socket_unstored_and_the_answer_comes_back(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live
) -> None:
    socket = await holding(registry, live)
    asking = asyncio.create_task(
        tenant_http.post(f"{DEV}/knowledge/knowledge.roster", json={"agent": AGENT})
    )
    request = await the_request(socket)
    assert (request.type, request.agent, request.call, request.ephemeral) == (
        "dev.request",
        AGENT,
        None,
        True,
    )
    assert request.data["verb"] == "knowledge.roster"
    assert request.data["data"] == {"agent": AGENT}
    assert live.dev_answered(DevAnswer(id=request.data["id"], result={"files": 3, "base": AGENT}))
    answer = await asking
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"files": 3, "base": AGENT}


async def test_the_apps_refusal_is_the_consoles_status_and_sentence_verbatim(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live
) -> None:
    socket = await holding(registry, live)
    asking = asyncio.create_task(
        tenant_http.post(f"{DEV}/chat/chat.start", json={"agent": "tienda"})
    )
    request = await the_request(socket)
    refused = DevRefusal(status=409, detail="this console runs in clinica-norte's directory")
    assert live.dev_answered(DevAnswer(id=request.data["id"], refused=refused))
    answer = await asking
    assert (answer.status_code, answer.json()["detail"]) == (409, refused.detail)


async def test_the_panel_beside_a_conversation_is_asked_for_with_the_calls_scope(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live
) -> None:
    socket = await holding(registry, live)
    asking = asyncio.create_task(
        tenant_http.post(
            f"{DEV}/view/view.render", json={"contact": "+34600000001", "call": "CA_1"}
        )
    )
    request = await the_request(socket)
    assert request.data["verb"] == "view.render"
    assert request.data["data"] == {"contact": "+34600000001", "call": "CA_1"}
    drawn = {"name": "Cliente", "nodes": [{"tag": "text", "text": "Dana"}]}
    assert live.dev_answered(DevAnswer(id=request.data["id"], result=drawn))
    answer = await asking
    assert (answer.status_code, answer.json()) == (200, drawn)


async def test_a_verb_that_is_not_one_of_the_family_is_refused_before_any_app_is_asked(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live
) -> None:
    socket = await holding(registry, live)
    answer = await tenant_http.post(f"{DEV}/knowledge/chat.start", json={})
    assert answer.status_code == 404
    assert "no dev verb chat.start in knowledge" in answer.json()["detail"]
    assert socket.heard == []


async def test_nobody_holding_the_agent_is_404_and_a_console_alone_is_409(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live
) -> None:
    nobody = await tenant_http.post(f"{DEV}/evals/drift.read", json={})
    assert nobody.status_code == 404
    socket = await holding(registry, live, takes_unclaimed=False)
    console_only = await tenant_http.post(f"{DEV}/evals/drift.read", json={})
    assert console_only.status_code == 409
    assert "pinecall start" in console_only.json()["detail"]
    # Named by its app id, the console IS asked: that is how a developer's own terminal mounts.
    named = asyncio.create_task(tenant_http.post(f"{DEV}/evals/drift.read?app={AN_OWNER}", json={}))
    request = await the_request(socket)
    assert live.dev_answered(DevAnswer(id=request.data["id"], result={"drift": {}}))
    assert (await named).status_code == 200


async def test_an_app_that_never_answers_is_a_504_in_a_sentence_and_the_ask_is_forgotten(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "ANSWERED_WITHIN_S", 0.05)
    socket = await holding(registry, live)
    answer = await tenant_http.post(f"{DEV}/memory/memory.roster", json={})
    assert answer.status_code == 504
    assert "did not answer memory.roster" in answer.json()["detail"]
    request = await the_request(socket)
    assert not live.dev_answered(DevAnswer(id=request.data["id"], result={})), "nobody waits now"


async def test_an_app_that_left_is_a_502(
    tenant_http: httpx.AsyncClient, registry: Registry, live: Live
) -> None:
    await holding(registry, live)
    live.disconnect(AN_OWNER)
    answer = await tenant_http.post(f"{DEV}/evals/goldens.roster", json={})
    assert answer.status_code == 502
    assert "disconnected" in answer.json()["detail"]


def test_every_dev_verb_is_in_exactly_one_family() -> None:
    families = list(dev.FAMILIES.values())
    assert frozenset().union(*families) == dev.VERBS
    assert sum(len(one) for one in families) == len(dev.VERBS)
