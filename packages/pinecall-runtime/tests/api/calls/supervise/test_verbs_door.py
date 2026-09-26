"""POST /v1/calls/{call}/verbs and the same verbs down WS /v1/attach: who may, and what lands."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.api.agents.registry import Registry
from pinecall.api.calls.events import BAD_VERB, VERB_REFUSED
from pinecall.api.live import Live
from pinecall.auth.keys import KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION, SANDBOX
from pinecall_protocol import Command, decode_entries, defs
from pinecall_protocol.commands import SupervisorVerb
from pinecall_protocol.fixtures import GOLDEN_LOG
from pinecall_protocol.verbs import SayVerb
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import a_context

pytestmark = pytest.mark.unit

THE_CALL = "call_somebody_is_on"
ANOTHER_CALL = "call_of_the_shop_next_door"
AN_OWNER = "app_holding_the_clinic"

# A second tenant on the same gateway, so "another org's key" is a real key and not a bad one.
ANOTHER_KEY = "pk_test_the_shop_next_door"
ANOTHER_ORG = KeyRecord(key_id="k_2", org="tienda")

# What a console holds: the key a person's browser was given at login, which is minted into the
# SANDBOX whatever world the page is reading (api/accounts/login.py), and names the member behind
# it.
A_PERSONS_KEY = "pk_the_browser_of_somebody_at_the_clinic"
A_PERSON = KeyRecord(key_id="k_3", org=A_RECORD.org, env=SANDBOX, subject="mem_ana", name="Ana")

SAY: dict[str, Any] = {"verb": "say", "text": "Decile que el turno quedó a las diez."}


@pytest.fixture
def keys() -> MemoryKeys:
    """Two tenants, so a verb aimed across the fence is refused by a key that is otherwise real."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_ORG, A_PERSONS_KEY: A_PERSON})


# ── the world one verb needs ────────────────────────────────────────────────────


async def a_live_call(
    store: MemoryStore, registry: Registry, live: Live, logs: Logs, call: str = THE_CALL
) -> None:
    """The clinic held by its app socket, and a call of it up to the caller's first turn."""
    if registry.of(PRODUCTION, AGENT) is None:
        await registry.register(AN_OWNER, A_RECORD.org, PRODUCTION, AGENT)
    # The claim the open door makes, and what says whose call this is: a verb is refused by the
    # log's own org, never by who is holding the agent's socket at the moment it is sent.
    await store.owned(call, AGENT, A_RECORD.org, PRODUCTION, "")
    for entry in decode_entries(GOLDEN_LOG.read_text()):
        await store.append(call=call, agent=AGENT, type=entry.type, data=entry.data)
        if entry.type == "turn.user":
            break
    held = registry.of(PRODUCTION, AGENT)
    assert held is not None
    live.serve(
        call,
        AGENT,
        A_RECORD.org,
        logs.writing(call, AGENT),
        None,
        context=a_context(call),
        config=held.config,
    )


async def a_call_that_ended(store: MemoryStore, registry: Registry, live: Live, logs: Logs) -> None:
    """The whole golden log, sealed on its score: nobody is on that line any more."""
    await a_live_call(store, registry, live, logs)
    for entry in decode_entries(GOLDEN_LOG.read_text()):
        await store.append(call=THE_CALL, agent=AGENT, type=entry.type, data=entry.data)


def sent(gateway: TestClient, call: str, said: dict[str, Any], bearer: str) -> Any:
    handle: Any = gateway
    return handle.post(
        f"/v1/calls/{call}/verbs", json=said, headers={"Authorization": f"Bearer {bearer}"}
    )


def a_supervise_token(gateway: TestClient, call: str) -> str:
    """The desk's own token for one call, minted by the door the tenant's key opens."""
    handle: Any = gateway
    answer: Any = handle.post(
        f"/v1/calls/{call}/supervise", headers={"Authorization": f"Bearer {A_KEY}"}
    )
    token: str = answer.json()["participant_token"]
    return token


def queued(live: Live, call: str) -> list[Command]:
    """Every command waiting for the worker of this call, taken off the queue in order."""
    waiting = live.commands(call)
    assert waiting is not None
    taken: list[Command] = []
    while True:
        try:
            command = waiting.get_nowait()
        except asyncio.QueueEmpty:
            return taken
        if command is not None:
            taken.append(command)


# ── the key, the token, and everybody else ──────────────────────────────────────


async def test_the_orgs_key_lands_the_verb_on_the_worker_queue_naming_the_org(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    answer = sent(gateway, THE_CALL, SAY, A_KEY)
    assert answer.status_code == 202
    assert answer.json() == {"call": THE_CALL, "verb": "say", "seq": None}
    (command,) = queued(live, THE_CALL)
    assert (command.type, command.agent, command.call) == ("supervisor.verb", AGENT, THE_CALL)
    said = SupervisorVerb.model_validate(command.data)
    assert said.by.id == "key:clinica"
    assert isinstance(said.verb, SayVerb) and said.verb.text == SAY["text"]


async def test_the_body_may_not_name_who_sent_it(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    """Authority is the token or the key: a body carrying `by` is refused by the schema itself."""
    await a_live_call(store, registry, live, logs)
    answer = sent(gateway, THE_CALL, SAY | {"by": {"id": "sup_i_wish"}}, A_KEY)
    assert answer.status_code == 422
    assert queued(live, THE_CALL) == []


async def test_another_orgs_key_is_403_and_its_verb_never_reaches_the_worker(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    answer = sent(gateway, THE_CALL, SAY, ANOTHER_KEY)
    assert answer.status_code == 403
    assert "another org" in answer.json()["detail"]
    assert queued(live, THE_CALL) == []


async def test_a_persons_key_steers_the_orgs_own_production_call(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    """The desk in the console: the key is the sandbox's, the call is production's, and both are
    the one org's. Who is holding the agent's socket has nothing to say about it."""
    await a_live_call(store, registry, live, logs)
    assert sent(gateway, THE_CALL, SAY, A_PERSONS_KEY).status_code == 202
    (command,) = queued(live, THE_CALL)
    assert SupervisorVerb.model_validate(command.data).by == defs.Supervisor(
        id="mem_ana", name="Ana"
    )


async def test_a_supervise_token_for_that_call_sends_the_verb_under_its_own_identity(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    token = a_supervise_token(gateway, THE_CALL)
    assert sent(gateway, THE_CALL, SAY, token).status_code == 202
    (command,) = queued(live, THE_CALL)
    assert SupervisorVerb.model_validate(command.data).by.id.startswith("sup_")


async def test_a_supervise_token_for_another_call_is_403_at_the_call_it_was_aimed_at(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    await a_live_call(store, registry, live, logs, call=ANOTHER_CALL)
    token = a_supervise_token(gateway, ANOTHER_CALL)
    answer = sent(gateway, THE_CALL, SAY, token)
    assert answer.status_code == 403
    assert queued(live, THE_CALL) == []


async def test_no_bearer_at_all_is_401(gateway: TestClient) -> None:
    handle: Any = gateway
    answer: Any = handle.post(f"/v1/calls/{THE_CALL}/verbs", json=SAY)
    assert answer.status_code == 401


# ── the call itself ─────────────────────────────────────────────────────────────


async def test_a_call_nobody_here_is_running_is_404(gateway: TestClient) -> None:
    answer = sent(gateway, "call_nobody", SAY, A_KEY)
    assert answer.status_code == 404 and "call_nobody" in answer.json()["detail"]


async def test_a_call_that_ended_is_409_and_says_where_to_read_it(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_call_that_ended(store, registry, live, logs)
    answer = sent(gateway, THE_CALL, SAY, A_KEY)
    assert answer.status_code == 409 and "recording" in answer.json()["detail"]
    assert queued(live, THE_CALL) == []


async def test_a_body_that_is_not_one_of_the_six_verbs_is_refused_with_the_reason(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    answer = sent(gateway, THE_CALL, {"verb": "shout", "text": "¡che!"}, A_KEY)
    assert answer.status_code == 422
    assert queued(live, THE_CALL) == []


# ── the same verbs down the socket ──────────────────────────────────────────────


async def test_a_verb_frame_on_the_attach_socket_lands_the_very_same_command(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    with gateway.websocket_connect(
        f"/v1/attach?call={THE_CALL}", headers={"Authorization": f"Bearer {A_KEY}"}
    ) as socket:
        _caught_up(socket)
        socket.send_json(SAY)
    (command,) = queued(live, THE_CALL)
    assert command.type == "supervisor.verb"
    assert SupervisorVerb.model_validate(command.data).by.id == "key:clinica"


async def test_a_verb_the_socket_will_not_apply_comes_back_as_an_error_entry(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    """The call this socket is tailing has ended: the same 409 sentence, in the log's own shape."""
    await a_call_that_ended(store, registry, live, logs)
    with gateway.websocket_connect(
        f"/v1/attach?call={THE_CALL}", headers={"Authorization": f"Bearer {A_KEY}"}
    ) as socket:
        # No draining first: a sealed log's tail ends at call.score, so the refusal is read out of
        # whatever the socket is still sending down.
        socket.send_json(SAY)
        refusal: Any = _the_error(socket)
    assert refusal["type"] == "error" and refusal["seq"] == 0
    assert refusal["data"]["code"] == VERB_REFUSED and "recording" in refusal["data"]["message"]
    assert queued(live, THE_CALL) == []


async def test_a_junk_frame_is_an_error_entry_and_never_a_command(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    with gateway.websocket_connect(
        f"/v1/attach?call={THE_CALL}", headers={"Authorization": f"Bearer {A_KEY}"}
    ) as socket:
        _caught_up(socket)
        socket.send_json({"say": "esto no es un verbo"})
        refusal: Any = socket.receive_json()
    assert refusal["data"]["code"] == BAD_VERB
    assert queued(live, THE_CALL) == []


async def test_a_supervise_token_for_another_call_never_opens_the_socket(
    gateway: TestClient, store: MemoryStore, registry: Registry, live: Live, logs: Logs
) -> None:
    await a_live_call(store, registry, live, logs)
    await a_live_call(store, registry, live, logs, call=ANOTHER_CALL)
    token = a_supervise_token(gateway, ANOTHER_CALL)
    with (
        pytest.raises(WebSocketDisconnect),
        gateway.websocket_connect(f"/v1/attach?call={THE_CALL}&token={token}"),
    ):
        pass
    assert queued(live, THE_CALL) == []


def _caught_up(socket: Any) -> None:
    """Drain the tail so the next frame the test reads is the answer to what it sent."""
    while socket.receive_json()["type"] != "log.caught_up":
        continue


def _the_error(socket: Any) -> Any:
    """The first error entry the socket sends, past whatever of the log is still coming down."""
    while (frame := socket.receive_json())["type"] != "error":
        continue
    return frame
