"""What an operator turned at the pipeline door is on a text call, not only on a voice one."""

from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall.types import Model
from tests.api.conftest import (
    A_KEY,
    AGENT,
    PIPELINE_KNOBS,
    a_call_the_app_ends,
    a_frame,
    an_app,
    declared,
)

pytestmark = pytest.mark.unit

DECLARED = {"provider": "anthropic", "model": "claude-haiku-4-5"}
TURNED = "anthropic/claude-sonnet-4-5"


# The bug found against a real gateway: the chat door read the held config raw, so an
# operator's knob was on every voice call and on no text one.
def test_a_text_call_is_built_with_the_model_the_operator_turned(
    gateway: TestClient, models_asked: list[Model | None]
) -> None:
    with an_app(gateway) as app_socket:
        _declaring(app_socket, DECLARED)
        assert _turning(gateway, {"llm": TURNED}) == 200
        a_call_the_app_ends(gateway, app_socket)
    assert models_asked[-1] == Model(provider="anthropic", model="claude-sonnet-4-5")


def test_a_text_call_with_no_knob_turned_is_built_with_the_model_the_app_declared(
    gateway: TestClient, models_asked: list[Model | None]
) -> None:
    with an_app(gateway) as app_socket:
        _declaring(app_socket, DECLARED)
        a_call_the_app_ends(gateway, app_socket)
    assert models_asked[-1] == Model(provider="anthropic", model="claude-haiku-4-5")


def _declaring(app_socket: WebSocketTestSession, llm: dict[str, str]) -> None:
    """The clinic on air, having declared the model the app itself asked to answer with."""
    declared(app_socket)
    app_socket.send_json(a_frame("agent.configure", AGENT, {"config": {"llm": llm}}))
    app_socket.receive_json()


# starlette's TestClient is an httpx client with no stubs for the members a test uses; the same
# deliberately untyped handle tests/api/conftest.py's got() goes through.
def _turning(gateway: TestClient, knobs: dict[str, str]) -> int:
    """The operator's PUT at the agent's pipeline door, as a status."""
    handle: Any = gateway
    answered: Any = handle.put(
        PIPELINE_KNOBS, json=knobs, headers={"Authorization": f"Bearer {A_KEY}"}
    )
    status: int = answered.status_code
    return status
