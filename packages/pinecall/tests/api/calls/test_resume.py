"""A written call its gateway forgot — a restart — is taken up from its log, not started again."""

from __future__ import annotations

import time
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.api.calls.chat import hung_up_by
from pinecall.log.writers import Logs
from pinecall_testkit.fake_llm import FakeLLM, Scripted
from tests.api.conftest import A_KEY, A_RECORD, AGENT, CHAT
from tests.api.talking import an_app, declared, entry_until

pytestmark = pytest.mark.unit

CALL = "call_the_gateway_forgot"


async def a_call_the_gateway_forgot(logs: Logs, sealed: bool = False) -> None:
    """What a restart leaves: the call's log whole, its head row unsealed, and nobody running it."""
    await logs.owned(CALL, AGENT, A_RECORD.org, A_RECORD.env, None)
    log = logs.writing(CALL, AGENT)
    line = {"channel": "web", "from": "web_ana", "to": AGENT, "caller": None}
    await log.append("call.started", {**line, "direction": "inbound", "started_at": time.time()})
    await log.append("turn.user", {"speech_id": "sp_1", "text": "Hola, quiero turno."})
    await log.append("turn.agent", {"speech_id": "sp_2", "text": "¿Para qué día?"})
    await log.append("state.changed", {"state": {"day": None}, "changed": ["day"]})
    if sealed:
        await log.append("call.score", {"judges": []})
    logs.forget(CALL)


def coming_back(gateway: TestClient, call: str = CALL) -> Any:
    """The caller's socket again, naming the call it was on."""
    return gateway.websocket_connect(
        f"{CHAT}?agent={AGENT}&call={call}", headers={"Authorization": f"Bearer {A_KEY}"}
    )


async def test_a_caller_back_on_a_forgotten_call_carries_on_with_its_whole_history(
    gateway: TestClient, logs: Logs, llm: FakeLLM
) -> None:
    await a_call_the_gateway_forgot(logs)
    llm.script.append(Scripted(chunks=("El martes a las diez.",)))
    with an_app(gateway) as app_socket:
        declared(app_socket)
        with coming_back(gateway) as caller:
            attached = entry_until(app_socket, "call.attached")
            assert attached["call"] == CALL
            assert attached["data"]["state"] == {"day": None}
            caller.send_json({"text": "El martes."})
            said = entry_until(caller, "turn.agent")
    assert said["call"] == CALL
    assert said["data"]["text"] == "El martes a las diez."
    heard = [message.text_content for message in llm.asked[-1].history]
    assert heard[:3] == ["Hola, quiero turno.", "¿Para qué día?", "El martes."]
    assert said["data"]["speech_id"] == "sp_3", "numbering goes on after the log's own"


async def test_nothing_is_started_again_on_a_call_taken_up(
    gateway: TestClient, logs: Logs, store: Any
) -> None:
    await a_call_the_gateway_forgot(logs)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        with coming_back(gateway):
            entry_until(app_socket, "call.attached")
    types = [entry.type for entry in await store.since(CALL)]
    assert types.count("call.started") == 1
    assert "turn.agent" not in types[types.index("call.attached") :], "no greeting said again"


async def test_a_call_that_is_over_is_not_taken_up(gateway: TestClient, logs: Logs) -> None:
    await a_call_the_gateway_forgot(logs, sealed=True)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        with coming_back(gateway) as caller, pytest.raises(WebSocketDisconnect) as closed:
            caller.receive_json()
    assert "cannot be taken up" in str(closed.value.reason)


# Starlette's TestClient cancels the door the moment its socket closes, so neither ending can be
# watched through it; the rule is what is tested, and the box is where the whole of it was run.
@pytest.mark.parametrize(
    ("code", "hung_up"), [(1012, False), (1000, True), (1001, True), (1006, True)]
)
def test_only_a_gateway_stopping_is_not_the_caller_hanging_up(code: int, hung_up: bool) -> None:
    assert hung_up_by(code) is hung_up
