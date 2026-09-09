"""The outside world in text: an event from the app, a reply on demand, a line said verbatim."""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from livekit.agents.llm import ChatMessage
from starlette.testclient import TestClient, WebSocketTestSession

from tests.api.calls.test_chat import A_TOOL, AN_EVENT
from tests.api.conftest import AGENT
from tests.api.talking import a_caller, a_frame, an_app, declared
from tests.session.fake_llm import FakeLLM, Scripted, a_call

pytestmark = pytest.mark.unit

# Short enough that a test can wait for it, long enough that a busy machine does not trip it.
A_QUICK_TOOL = {**A_TOOL, "timeout_s": 0.2}

# What the app writes into the identity block, the one static block a call opens with here.
CLARA = "Sos Clara, de la clínica."


# ── the outside world ───────────────────────────────────────────────────────────


def test_a_declared_event_lands_as_event_received_and_reaches_the_app(
    gateway: TestClient, llm: FakeLLM
) -> None:
    with _a_call(gateway, events=(AN_EVENT,)) as talking:
        talking.app_says("call.event", {"name": "slot_freed", "data": {"at": "10:15"}})
        received = talking.until("event.received")
    assert received["data"] == {"name": "slot_freed", "data": {"at": "10:15"}, "source": "app"}
    assert received["call"] == talking.call
    assert llm.requests == 0


def test_an_undeclared_event_is_refused_by_name_and_never_reaches_the_log(
    gateway: TestClient,
) -> None:
    with _a_call(gateway, events=(AN_EVENT,)) as talking:
        talking.app_says("call.event", {"name": "meteorite", "data": {}})
        refusal = talking.until("error")
    assert refusal["data"]["code"] == "refused"
    assert "meteorite" in refusal["data"]["message"]
    assert refusal["call"] is None
    assert "event.received" not in [entry["type"] for entry in talking.heard]


def test_a_state_set_after_an_event_says_the_event_caused_it(gateway: TestClient) -> None:
    with _a_call(gateway, events=(AN_EVENT,)) as talking:
        talking.app_says("call.event", {"name": "slot_freed", "data": {}})
        received = talking.until("event.received")
        talking.app_says("state.set", {"state": {"slot": "10:15"}})
        changed = talking.until("state.changed")
    assert changed["data"]["state"] == {"slot": "10:15"}
    assert changed["data"]["changed"] == ["slot"]
    assert changed["data"]["cause"] == {
        "kind": "event",
        "name": "slot_freed",
        "seq": received["seq"],
    }


def test_a_state_set_inside_a_tool_says_the_tool_caused_it(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The app moves its state from inside the tool, before it answers; the log says which tool."""
    llm.script.extend(_a_tool_turn())
    with _a_call(gateway, tools=(A_TOOL,)) as talking:
        talking.caller.send_json({"text": "¿martes?"})
        asked = talking.until("tool.call")
        talking.app_says("state.set", {"state": {"looked": True}})
        changed = talking.until("state.changed")
        talking.answers(asked, "martes 10:15")
        talking.until("turn.agent")
    assert changed["data"]["cause"] == {"kind": "tool", "tool": "find_slots", "call_id": "tu_1"}


def test_a_tool_that_never_answers_lapses_and_the_model_is_told(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(_a_tool_turn())
    with _a_call(gateway, tools=(A_QUICK_TOOL,)) as talking:
        talking.caller.send_json({"text": "¿martes?"})
        talking.until("tool.call")
        lapsed = talking.until("tool.result")
        talking.until("turn.agent")
    assert "did not answer within" in lapsed["data"]["error"]
    assert llm.asked[-1].outputs[-1].is_error


# The agent declares confirm and side_effect on an irreversible tool exactly as before: the wire
# still carries both. What no longer exists is anything on the platform that reads them at run
# time, and this is where that is asserted from the outside.
A_GATED_TOOL = {
    "name": "book",
    "description": "Book an appointment",
    "parameters": {"type": "object", "properties": {"at": {"type": "string"}}},
    "timeout_s": 5,
    "side_effect": "irreversible",
    "confirm": "Le reservo el {at}. ¿Confirmo?",
}


def test_a_tool_declared_with_confirm_runs_straight_through_with_no_confirmation(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The gate is gone: an irreversible tool reaches the app on the first turn that calls it."""
    llm.script.extend(
        (
            Scripted(chunks=("Voy. ",), calls=(a_call("bk_1", "book", {"at": "10:15"}),)),
            Scripted(chunks=("Reservado.",)),
        )
    )
    with _a_call(gateway, tools=(A_GATED_TOOL,)) as talking:
        talking.caller.send_json({"text": "reservame el turno"})
        asked = talking.until("tool.call")
        talking.answers(asked, "ok")
        talking.until("turn.agent")
    types = [entry["type"] for entry in talking.heard]
    assert not [type for type in types if type.startswith("confirm.")]
    assert types.index("tool.call") < types.index("tool.result")
    assert asked["data"]["name"] == "book"


def test_a_tool_result_nobody_is_waiting_for_is_refused(gateway: TestClient) -> None:
    with _a_call(gateway, tools=(A_TOOL,)) as talking:
        talking.app_says("tool.result", {"call_id": "tu_9", "name": "find_slots", "output": "x"})
        refusal = talking.until("error")
    assert "tu_9" in refusal["data"]["message"]


# ── the two ways an app makes the agent speak ───────────────────────────────────


def test_agent_reply_produces_exactly_one_turn_agent_and_no_turn_user(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=("Se liberó un turno a las 10:15.",)))
    with _a_call(gateway) as talking:
        talking.app_says("agent.reply", {"instructions": "avisá del turno libre"})
        said = talking.until("turn.agent")
    types = [entry["type"] for entry in talking.heard]
    assert types.count("turn.agent") == 1
    assert "turn.user" not in types
    assert said["data"]["text"] == "Se liberó un turno a las 10:15."


def test_the_instruction_of_an_agent_reply_reaches_the_model_as_a_user_message(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The model must read it, and the log must never call it something the caller said."""
    llm.script.append(Scripted(chunks=("Listo.",)))
    with _a_call(gateway) as talking:
        talking.app_says("agent.reply", {"instructions": "avisá del turno libre"})
        talking.until("turn.agent")
    asked = llm.asked[0].history[-1]
    assert asked.role == "user"
    assert asked.text_content == "avisá del turno libre"


def test_agent_say_produces_one_turn_agent_verbatim_with_no_model_call(
    gateway: TestClient, llm: FakeLLM
) -> None:
    with _a_call(gateway) as talking:
        talking.app_says("agent.say", {"text": "Un momento."})
        said = talking.until("turn.agent")
    assert said["data"]["text"] == "Un momento."
    assert said["data"]["metrics"]["e2e_latency"] >= 0
    assert "llm_node_ttft" not in said["data"]["metrics"]
    assert llm.requests == 0


# ── what the app sets on the session ────────────────────────────────────────────


def test_prompt_set_logs_the_hash_and_the_length_and_never_the_text(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=("Hola.",)))
    with _a_call(gateway) as talking:
        talking.app_says("prompt.set", {"name": "identity", "text": CLARA})
        talking.until("prompt.changed")
        talking.app_says("prompt.set", {"name": "view", "text": "El turno es a las 10:15"})
        changed = talking.until("prompt.changed")
        talking.caller.send_json({"text": "hola"})
        talking.until("turn.agent")
    assert changed["data"]["name"] == "view"
    assert changed["data"]["chars"] == len("El turno es a las 10:15")
    assert "10:15" not in str(changed["data"])
    system = llm.asked[0].system
    assert CLARA in system
    assert "El turno es a las 10:15" in system
    # The blocks go in order: the static ones the app wrote, then the view it rendered.
    assert system.index("Sos Clara") < system.index("El turno")


def test_a_block_the_agent_never_declared_is_refused_by_name(gateway: TestClient) -> None:
    with _a_call(gateway) as talking:
        talking.app_says("prompt.set", {"name": "faq", "text": "Abrimos a las nueve."})
        refusal = talking.until("error")
    assert refusal["data"]["code"] == "refused"
    assert "'faq'" in refusal["data"]["message"]
    assert "identity, knowledge, tools, view" in refusal["data"]["message"]
    assert "prompt.changed" not in [entry["type"] for entry in talking.heard]


# The prompt in livekit's terms: `instructions` is the Agent's own, the static blocks joined once
# and rebuilt only when one of them moves, and the dynamic blocks are appended to the request
# after the history. A view that moves every turn must therefore leave the instructions byte for
# byte identical, which is what a cache is for.
def test_the_view_moves_without_touching_the_cached_instructions(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend([Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",))])
    with _a_call(gateway) as talking:
        talking.app_says("prompt.set", {"name": "identity", "text": CLARA})
        talking.until("prompt.changed")
        talking.app_says("prompt.set", {"name": "view", "text": "El turno es a las 10:15"})
        talking.until("prompt.changed")
        talking.caller.send_json({"text": "hola"})
        talking.until("turn.agent")
        talking.app_says("prompt.set", {"name": "view", "text": "El turno es a las 11:45"})
        talking.until("prompt.changed")
        talking.caller.send_json({"text": "¿y ahora?"})
        talking.until("turn.agent")
    first, second = llm.asked[0], llm.asked[1]
    assert first.instructions == CLARA
    assert second.instructions == first.instructions, "the cached prefix must not move"
    assert "10:15" in first.system and "10:15" not in first.instructions
    assert "11:45" in second.system and "11:45" not in second.instructions
    # And it is the LAST thing the model reads, after everything that has been said.
    last = second.chat_ctx.items[-1]
    assert isinstance(last, ChatMessage)
    assert last.text_content == "El turno es a las 11:45"
    # And only ONE view per request: livekit hands llm_node a copy of the history, so the view
    # appended there never lands in the history and never piles up request after request.
    views = [
        item
        for item in second.chat_ctx.items
        if isinstance(item, ChatMessage) and "El turno es a las" in (item.text_content or "")
    ]
    assert len(views) == 1, "the view accumulated in the history"


def test_tools_set_narrows_what_the_model_may_call_to_what_was_declared(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=("Hola.",)))
    with _a_call(gateway, tools=(A_TOOL,)) as talking:
        undeclared = {**A_TOOL, "name": "never_declared"}
        talking.app_says("tools.set", {"tools": [A_TOOL, undeclared]})
        changed = talking.until("tools.changed")
        talking.caller.send_json({"text": "hola"})
        talking.until("turn.agent")
    assert changed["data"]["visible"] == ["find_slots"]
    assert llm.asked[0].tools == ("find_slots",)


def test_session_configure_sets_the_calls_state_before_the_first_turn(
    gateway: TestClient,
) -> None:
    with _a_call(gateway) as talking:
        talking.app_says("session.configure", {"state": {"patient": "nobody yet"}})
        changed = talking.until("state.changed")
    assert changed["data"]["state"] == {"patient": "nobody yet"}
    assert "cause" not in changed["data"]


def test_a_call_scoped_command_naming_a_call_nobody_runs_is_refused(gateway: TestClient) -> None:
    with an_app(gateway) as app_socket:
        declared(app_socket)
        app_socket.send_json(a_frame("agent.say", AGENT, {"text": "hola"}, call="call_nowhere"))
        refusal = app_socket.receive_json()
    assert refusal["data"]["code"] == "no_session"
    assert "call_nowhere" in refusal["data"]["message"]


# ── one open call, for a test that cares about one thing in it ──────────────────


@dataclass
class Talking:
    """One call in flight: the app's socket, the caller's, and every entry the app has heard."""

    app: WebSocketTestSession
    caller: WebSocketTestSession
    heard: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    @property
    def call(self) -> str:
        """The id the gateway minted, read off the first entry of the call."""
        return str(self.heard[0]["call"])

    def app_says(self, type: str, data: dict[str, Any]) -> None:
        """One call-scoped command from the tenant's process, on the call this test opened."""
        self.app.send_json(a_frame(type, AGENT, data, call=self.call, id=f"cmd_{type}"))

    def answers(self, asked: dict[str, Any], output: str) -> None:
        """The app's process ran the tool the model called, and this is what it returned."""
        self.app_says(
            "tool.result",
            {"call_id": asked["data"]["call_id"], "name": asked["data"]["name"], "output": output},
        )

    def until(self, type: str, most: int = 40) -> dict[str, Any]:
        """Read the app's socket, keeping every entry, until the one this test is waiting for."""
        for _ in range(most):
            entry: dict[str, Any] = self.app.receive_json()
            self.heard.append(entry)
            if entry["type"] == type:
                return entry
        raise AssertionError(f"the app never heard {type}")


@contextmanager
def _a_call(
    gateway: TestClient,
    tools: Sequence[Mapping[str, object]] = (),
    events: Sequence[Mapping[str, object]] = (),
) -> Generator[Talking]:
    """An agent declared, a caller on the line, the call started: where each test below opens."""
    with an_app(gateway) as app_socket:
        declared(app_socket, tools=tools, events=events)
        with a_caller(gateway) as caller:
            talking = Talking(app_socket, caller)
            talking.until("call.started")
            yield talking


def _a_tool_turn() -> tuple[Scripted, ...]:
    """A turn that calls find_slots once, and the turn that answers with what came back."""
    return (
        Scripted(
            chunks=("Veamos. ",),
            calls=(a_call("tu_1", "find_slots", {"day": "martes"}),),
        ),
        Scripted(chunks=("Hay a las 10:15.",)),
    )
