"""A whole text call over the real sockets: three turns, one tool round-trip, and its log."""

from collections.abc import Callable
from typing import Any

import pytest
from livekit.agents.llm import CompletionUsage
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from pinecall.api.calls.chat import CLOSE_REASON_BYTES
from pinecall.auth.bearer import POLICY_VIOLATION
from pinecall_protocol import decode_entry, event_of
from pinecall_protocol.events import CallSummary
from pinecall_protocol.metrics import LLMMetrics
from tests.api.conftest import AGENT, CHAT, a_caller, a_frame, an_app, declared, entry_until
from tests.session.fake_llm import FakeLLM, Scripted, a_call

pytestmark = pytest.mark.unit

# What Anthropic reports on a request that wrote to its cache, and what a provider that reports
# neither cache number reports: the second one is how a field stays absent instead of turning to 0.
CACHED = CompletionUsage(
    prompt_tokens=1200,
    completion_tokens=24,
    prompt_cached_tokens=1024,
    cache_creation_tokens=176,
    total_tokens=1224,
)
UNCACHED = CompletionUsage(
    prompt_tokens=90, completion_tokens=12, prompt_cached_tokens=0, total_tokens=102
)

# The two tools the app declares, and the one the model calls in the second turn.
A_TOOL = {
    "name": "find_slots",
    "description": "Free appointments on a day",
    "parameters": {"type": "object", "properties": {"day": {"type": "string"}}},
    "timeout_s": 5,
}
ANOTHER_TOOL = {
    "name": "find_patient",
    "description": "Look a patient up by phone",
    "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}},
    "timeout_s": 5,
}

A_CALL_ID = "tu_1"

# The one outside fact this agent accepts, so the event tests have something declared to send.
AN_EVENT = {"name": "slot_freed", "from": ["app"]}


def a_script() -> tuple[Scripted, ...]:
    """A greeting, a turn that calls a tool and answers with what it returned, and a close."""
    return (
        Scripted(chunks=("Hola, ", "¿en qué puedo ayudarte?"), usage=CACHED, request_id="req_1"),
        Scripted(
            chunks=("Déjame ver. ",),
            calls=(a_call(A_CALL_ID, "find_slots", {"day": "martes"}),),
            usage=UNCACHED,
            request_id="req_2",
        ),
        Scripted(chunks=("Hay una a las 10:15.",), usage=CACHED, request_id="req_3"),
        Scripted(chunks=("Listo, agendado.",), usage=CACHED, request_id="req_4"),
    )


# ── the whole call ──────────────────────────────────────────────────────────────


def test_three_turns_with_one_tool_call_write_the_entries_the_protocol_names(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """Criterion 1: every entry of a finished call, by the registry's names and in order."""
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    assert [entry["type"] for entry in written] == [
        "call.started",
        # the greeting
        "turn.user",
        "agent.state",
        "agent.state",
        "agent.transcript",
        "agent.transcript",
        "metrics.llm",
        "turn.agent",
        "agent.state",
        # the turn that needs a tool
        "turn.user",
        "agent.state",
        "agent.state",
        "agent.transcript",
        "metrics.llm",
        "tool.call",
        "tool.result",
        "agent.state",
        "agent.state",
        "agent.transcript",
        "metrics.llm",
        "turn.agent",
        "agent.state",
        # the close
        "turn.user",
        "agent.state",
        "agent.state",
        "agent.transcript",
        "metrics.llm",
        "turn.agent",
        "agent.state",
        "call.ended",
        "call.summary",
    ]


def test_the_criterions_names_are_all_there_in_that_order(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The card's list, read as a subsequence of what the call actually wrote."""
    llm.script.extend(a_script())
    written = [entry["type"] for entry in _a_conversation(gateway)]
    wanted = [
        "call.started",
        "turn.user",
        "agent.state",
        "agent.transcript",
        "tool.call",
        "tool.result",
        "metrics.llm",
        "turn.agent",
        "call.ended",
        "call.summary",
    ]
    left = list(written)
    for name in wanted:
        assert name in left, f"{name} never reached the log"
        left = left[left.index(name) + 1 :]


def test_the_transcripts_are_ephemeral_and_the_turns_are_not(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    transcripts = [entry for entry in written if entry["type"] == "agent.transcript"]
    assert transcripts and all(entry["ephemeral"] for entry in transcripts)
    assert all(entry["data"]["final"] is False for entry in transcripts)
    assert all(not entry["ephemeral"] for entry in written if entry["type"] == "turn.agent")


def test_one_speech_id_joins_the_turn_its_transcripts_its_metrics_and_its_tool_call(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    second_turn = [entry for entry in written if entry["type"] == "turn.user"][1]
    speech = second_turn["data"]["speech_id"]
    joined = {
        entry["type"]
        for entry in written
        if entry["data"].get("speech_id") == speech and entry["type"] != "turn.user"
    }
    assert joined == {"agent.transcript", "metrics.llm", "tool.call", "turn.agent"}


def test_the_agent_state_goes_thinking_speaking_idle_and_repeats_nothing(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    states = [entry["data"]["state"] for entry in written if entry["type"] == "agent.state"]
    assert states[:3] == ["thinking", "speaking", "idle"]
    assert all(one != next_one for one, next_one in zip(states, states[1:], strict=False))


def test_the_tool_call_reaches_the_app_and_its_result_reaches_the_model(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The round trip is the point: the app ran it, and the next request carries its output."""
    llm.script.extend(a_script())
    _a_conversation(gateway)
    # The clock's pair opens every history, so the answer to THIS call is the last one there.
    answered = llm.asked[2].outputs[-1]
    assert answered.call_id == A_CALL_ID
    assert answered.output == "martes 10:15"
    assert not answered.is_error


# ── the metrics ─────────────────────────────────────────────────────────────────


def test_every_field_the_provider_reported_is_present_under_its_livekit_name(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """Criterion 3, the present half: nothing the provider said is summarised away or renamed."""
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    first = [entry for entry in written if entry["type"] == "metrics.llm"][0]["data"]
    assert set(LLMMetrics.model_fields) - set(first) == {"reasoning_tokens"}
    assert first["prompt_tokens"] == CACHED.prompt_tokens
    assert first["prompt_cached_tokens"] == CACHED.prompt_cached_tokens
    assert first["cache_creation_tokens"] == CACHED.cache_creation_tokens
    assert first["completion_tokens"] == CACHED.completion_tokens
    assert first["total_tokens"] == CACHED.total_tokens
    assert first["request_id"] == "req_1"
    assert first["metadata"] == {
        "model_name": "claude-haiku-4-5-20251001",
        "model_provider": "anthropic",
    }
    assert first["tokens_per_second"] > 0
    assert first["cancelled"] is False


def test_a_field_the_provider_never_reported_is_absent_and_never_zero(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """Criterion 3, the absent half: the second request's provider reported no cache write."""
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    second = [entry for entry in written if entry["type"] == "metrics.llm"][1]["data"]
    assert "cache_creation_tokens" not in second
    assert "reasoning_tokens" not in second
    assert second["prompt_cached_tokens"] == 0


def test_the_turn_carries_what_a_text_session_can_measure_and_nothing_audio_would(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    measured = [entry for entry in written if entry["type"] == "turn.agent"][0]["data"]["metrics"]
    assert measured["llm_node_ttft"] >= 0
    assert measured["e2e_latency"] >= measured["llm_node_ttft"]
    assert measured["provider_request_ids"] == ["req_1"]
    assert "tts_node_ttfb" not in measured
    assert "playback_latency" not in measured
    assert "started_speaking_at" not in measured


# ── the summary ─────────────────────────────────────────────────────────────────


def test_the_summary_carries_the_usage_rows_and_a_cost_line_in_euros(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(a_script())
    written = _a_conversation(gateway)
    summary = event_of(decode_entry(written[-1]))
    assert isinstance(summary, CallSummary)
    assert summary.turns == 6
    assert summary.reason == "agent_hung_up"
    row = summary.usage[0]
    assert row.type == "llm_usage"
    assert row.provider == "anthropic"
    assert row.input_tokens == 3 * 1200 + 90
    assert row.input_cache_creation_tokens == 3 * 176
    assert row.output_tokens == 3 * 24 + 12
    assert summary.cost.eur > 0
    assert summary.cost.rate.currency == "EUR"
    assert summary.cost.unpriced == []
    assert {line.unit for line in summary.cost.rows} == {
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_tokens",
        "output_tokens",
    }


# ── the door ────────────────────────────────────────────────────────────────────


def test_a_chat_socket_with_no_key_never_opens(gateway: TestClient) -> None:
    with an_app(gateway) as app_socket:
        declared(app_socket, tools=(A_TOOL, ANOTHER_TOOL), events=(AN_EVENT,))
        with pytest.raises(WebSocketDisconnect):
            with gateway.websocket_connect(f"{CHAT}?agent={AGENT}"):
                pass


def test_a_chat_socket_for_an_agent_nobody_registered_is_closed_with_a_reason(
    gateway: TestClient,
) -> None:
    """The caller reads a sentence naming the agent: a refusal with no words cannot be acted on."""
    with a_caller(gateway, agent="nobody-is-here") as caller:
        with pytest.raises(WebSocketDisconnect) as refused:
            caller.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert "nobody-is-here" in refused.value.reason


def test_a_refusal_too_long_for_a_close_frame_is_cut_rather_than_lost(gateway: TestClient) -> None:
    """A reason over 123 bytes is refused by the library, and the caller reads none at all."""
    with a_caller(gateway, agent="a" * 200) as caller:
        with pytest.raises(WebSocketDisconnect) as refused:
            caller.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert len(refused.value.reason.encode()) == CLOSE_REASON_BYTES


def test_the_caller_is_sent_every_entry_of_their_own_call(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The caller reads the same entries the log keeps, unprojected: the sink projects, not this."""
    llm.script.append(Scripted(chunks=("Hola.",), usage=CACHED))
    with an_app(gateway) as app_socket:
        declared(app_socket, tools=(A_TOOL, ANOTHER_TOOL), events=(AN_EVENT,))
        with a_caller(gateway) as caller:
            assert caller.receive_json()["type"] == "call.started"
            caller.send_json({"text": "hola"})
            heard = [caller.receive_json() for _ in range(7)]
    assert [entry["type"] for entry in heard] == [
        "turn.user",
        "agent.state",
        "agent.state",
        "agent.transcript",
        "metrics.llm",
        "turn.agent",
        "agent.state",
    ]
    assert all(entry["seq"] > 0 for entry in heard)


# ── the conversation these tests all run ────────────────────────────────────────


# The app socket is a reader of the call like any other, so the entries it hears ARE the call's log,
# in the order the store numbered them. It is still open when the caller hangs up, which is how the
# last two entries of the call are read here at all.
def _a_conversation(gateway: TestClient) -> list[dict[str, Any]]:
    """Three turns, one tool round-trip, the caller hangs up. Returns every entry of the call."""
    heard: list[dict[str, Any]] = []
    with an_app(gateway) as app_socket:
        declared(app_socket, tools=(A_TOOL, ANOTHER_TOOL), events=(AN_EVENT,))
        listening = _listening(app_socket, heard)
        with a_caller(gateway) as caller:
            caller.send_json({"text": "hola"})
            caller.send_json({"text": "¿tenés hora el martes?"})
            asked = listening("tool.call")
            app_socket.send_json(
                a_frame(
                    "tool.result",
                    AGENT,
                    {
                        "call_id": asked["data"]["call_id"],
                        "name": asked["data"]["name"],
                        "output": "martes 10:15",
                    },
                    call=asked["call"],
                )
            )
            listening("turn.agent")
            caller.send_json({"text": "dale"})
            listening("turn.agent")
            # The app hangs up rather than the caller, because a TestClient websocket is cancelled
            # the moment its block exits and the session would be cut off mid-summary. The caller's
            # own hangup is the same two entries with reason caller_hung_up.
            app_socket.send_json(a_frame("call.hangup", AGENT, {}, call=heard[0]["call"]))
            listening("call.summary")
    return heard


def _listening(
    app_socket: WebSocketTestSession, heard: list[dict[str, Any]]
) -> Callable[[str], dict[str, Any]]:
    """This file's socket and its transcript, bound once, so a test names only what it waits for."""

    def until(type: str) -> dict[str, Any]:
        return entry_until(app_socket, type, keeping=heard)

    return until
