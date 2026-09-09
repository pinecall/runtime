"""The six verbs aimed at a call this process runs itself: what lands, and what nobody hears."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall.api._live import Live
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.session.supervising import ALREADY_HELD, NO_LINE_TO_TRANSFER, NOBODY_HOLDS
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import a_caller, a_frame, an_app, declared, entry_until
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

# A second tenant on the same gateway, so "another org's key" is a real key and not a bad one.
ANOTHER_KEY = "pk_test_the_shop_next_door"
ANOTHER_ORG = KeyRecord(key_id="k_2", org="tienda")

A_GREETING = "Hola, ¿en qué puedo ayudarte?"
AN_ANSWER = "Sí, atendemos los sábados."
THE_DESK_SAYS = "Te confirmo yo: el turno quedó a las diez."
THE_DESK_WHISPERS = "Decile que la clínica cierra a las ocho."


@pytest.fixture
def keys() -> MemoryKeys:
    """Two tenants, so a verb aimed across the fence is refused by a key that is otherwise real."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_ORG})


# ── say ─────────────────────────────────────────────────────────────────────────


def test_the_desks_say_writes_its_entry_first_and_then_the_agents_own_turn(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        assert sent(gateway, call, {"verb": "say", "text": THE_DESK_SAYS}).status_code == 202
        entry_until(app_socket, "turn.agent", keeping=heard)

    said = _typed(heard, "supervisor.said")[0]
    (spoken,) = _after(heard, "supervisor.said", "turn.agent")
    assert said["data"]["text"] == THE_DESK_SAYS
    assert said["data"]["by"]["id"] == "key:clinica"
    assert spoken["data"]["text"] == THE_DESK_SAYS
    assert said["seq"] < spoken["seq"]


def test_the_desks_say_asks_the_model_nothing_at_all(gateway: TestClient, llm: FakeLLM) -> None:
    """The supervisor's words are the agent's words, verbatim: no request goes out for them."""
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        asked = llm.requests
        sent(gateway, call, {"verb": "say", "text": THE_DESK_SAYS})
        entry_until(app_socket, "turn.agent", keeping=heard)
        assert llm.requests == asked


# ── whisper ─────────────────────────────────────────────────────────────────────


def test_a_whisper_lands_its_entry_and_the_next_request_carries_the_note(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend((Scripted(chunks=(A_GREETING,)), Scripted(chunks=(AN_ANSWER,))))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        whisper = {"verb": "whisper", "text": THE_DESK_WHISPERS}
        assert sent(gateway, call, whisper).status_code == 202
        entry_until(app_socket, "turn.agent", keeping=heard)

    whispered = _typed(heard, "supervisor.whispered")[0]
    assert whispered["data"]["text"] == THE_DESK_WHISPERS
    # Both halves: the note is in the history for the rest of the call, AND it is the instructions
    # of the turn it asked for — livekit appends those as a system message of that request alone.
    system = llm.asked[-1].system
    assert system.count(THE_DESK_WHISPERS) == 2
    assert "takes precedence over the stage instructions" in system


def test_a_whisper_never_enters_the_log_as_words_the_caller_wrote(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend((Scripted(chunks=(A_GREETING,)), Scripted(chunks=(AN_ANSWER,))))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        sent(gateway, call, {"verb": "whisper", "text": THE_DESK_WHISPERS})
        entry_until(app_socket, "turn.agent", keeping=heard)

    written = [entry["data"]["text"] for entry in _typed(heard, "turn.user")]
    assert THE_DESK_WHISPERS not in written
    # And not as anybody's words in the history either: it is a system message of that turn alone.
    assert all(THE_DESK_WHISPERS != said.text_content for said in llm.asked[-1].history)


# ── takeover and release ────────────────────────────────────────────────────────


def test_while_a_human_holds_the_thread_the_caller_is_logged_and_the_model_is_not_asked(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, caller, call):
        assert sent(gateway, call, {"verb": "takeover"}).status_code == 202
        entry_until(app_socket, "supervisor.took_over", keeping=heard)
        asked = llm.requests
        caller.send_json({"text": "¿y los sábados?"})
        entry_until(app_socket, "turn.user", keeping=heard)
        # Nothing more is coming: the proof is the say, which arrives after it and nothing between.
        sent(gateway, call, {"verb": "say", "text": THE_DESK_SAYS})
        entry_until(app_socket, "supervisor.said", keeping=heard)
        assert llm.requests == asked

    after = _after(heard, "supervisor.took_over", "turn.user", "turn.agent", "supervisor.said")
    assert [entry["type"] for entry in after] == ["turn.user", "supervisor.said", "turn.agent"]


def test_the_words_a_held_thread_heard_never_enter_the_models_history(
    gateway: TestClient, llm: FakeLLM
) -> None:
    """The same rule a spoken takeover goes deaf under: an honest hole, never an invented memory."""
    llm.script.extend((Scripted(chunks=(A_GREETING,)), Scripted(chunks=(AN_ANSWER,))))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, caller, call):
        sent(gateway, call, {"verb": "takeover"})
        entry_until(app_socket, "supervisor.took_over", keeping=heard)
        caller.send_json({"text": "¿y los sábados?"})
        entry_until(app_socket, "turn.user", keeping=heard)
        sent(gateway, call, {"verb": "release"})
        entry_until(app_socket, "turn.agent", keeping=heard)

    said = [message.text_content for message in llm.asked[-1].history]
    assert "¿y los sábados?" not in said


def test_a_release_hands_the_thread_back_and_the_agent_answers_the_next_one(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.extend(
        (
            Scripted(chunks=(A_GREETING,)),
            Scripted(chunks=("Sigo yo.",)),
            Scripted(chunks=(AN_ANSWER,)),
        )
    )
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, caller, call):
        sent(gateway, call, {"verb": "takeover"})
        entry_until(app_socket, "supervisor.took_over", keeping=heard)
        assert sent(gateway, call, {"verb": "release"}).status_code == 202
        entry_until(app_socket, "turn.agent", keeping=heard)
        caller.send_json({"text": "¿y los sábados?"})
        entry_until(app_socket, "turn.agent", keeping=heard)

    after = _after(heard, "supervisor.released", "turn.user", "turn.agent")
    assert [entry["type"] for entry in after] == ["turn.agent", "turn.user", "turn.agent"]
    assert "did not hear" in llm.asked[-2].system


def test_taking_a_thread_that_is_already_held_names_who_holds_it(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        sent(gateway, call, {"verb": "takeover"})
        entry_until(app_socket, "supervisor.took_over", keeping=heard)
        refused = sent(gateway, call, {"verb": "takeover"})
    assert refused.status_code == 409
    assert refused.json()["detail"] == ALREADY_HELD.format(id="key:clinica")


def test_releasing_a_thread_nobody_holds_is_refused_in_the_same_words(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (_app_socket, _caller, call):
        refused = sent(gateway, call, {"verb": "release"})
    assert refused.status_code == 409 and refused.json()["detail"] == NOBODY_HOLDS


# ── end and transfer ────────────────────────────────────────────────────────────


def test_the_desks_end_seals_the_call_saying_a_supervisor_hung_up(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        assert sent(gateway, call, {"verb": "end", "reason": "resuelto"}).status_code == 202
        entry_until(app_socket, "call.summary", keeping=heard)

    ended = _typed(heard, "supervisor.ended")[0]
    (sealed,) = _after(heard, "supervisor.ended", "call.ended")
    assert ended["data"]["reason"] == "resuelto"
    assert sealed["data"]["ended_by"] == "supervisor"
    assert sealed["data"]["reason"] == "supervisor_ended"


def test_a_transfer_is_refused_because_a_text_call_has_no_line_to_move(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (_app_socket, _caller, call):
        refused = sent(gateway, call, {"verb": "transfer", "to": "+34910000000", "mode": "cold"})
    assert refused.status_code == 409 and refused.json()["detail"] == NO_LINE_TO_TRANSFER
    assert not _typed(heard, "supervisor.transferred")


# ── whose call it is ────────────────────────────────────────────────────────────


def test_another_orgs_key_never_reaches_a_thread_of_this_one(
    gateway: TestClient, llm: FakeLLM
) -> None:
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (_app_socket, _caller, call):
        refused = sent(gateway, call, {"verb": "say", "text": THE_DESK_SAYS}, bearer=ANOTHER_KEY)
    assert refused.status_code == 403
    assert not _typed(heard, "supervisor.said")


def test_a_verb_applied_here_never_waits_on_a_worker_that_will_never_read_it(
    gateway: TestClient, llm: FakeLLM, live: Live
) -> None:
    """The queue exists for a worker-run call: a text call is applied and its queue stays empty."""
    llm.script.append(Scripted(chunks=(A_GREETING,)))
    heard: list[dict[str, Any]] = []
    with a_thread(gateway, heard) as (app_socket, _caller, call):
        sent(gateway, call, {"verb": "say", "text": THE_DESK_SAYS})
        entry_until(app_socket, "turn.agent", keeping=heard)
        waiting = live.commands(call)
        assert waiting is not None and waiting.empty()


# ── the world one verb needs ────────────────────────────────────────────────────


@contextmanager
def a_thread(
    gateway: TestClient, heard: list[dict[str, Any]]
) -> Generator[tuple[WebSocketTestSession, WebSocketTestSession, str]]:
    """The clinic held by its app socket, and one text call of it past the caller's first turn."""
    with an_app(gateway) as app_socket:
        declared(app_socket)
        with a_caller(gateway) as caller:
            caller.send_json({"text": "hola"})
            started = entry_until(app_socket, "call.started", keeping=heard)
            entry_until(app_socket, "turn.agent", keeping=heard)
            yield app_socket, caller, str(started["call"])
            # The app hangs up rather than the caller: a TestClient websocket is cancelled the
            # moment its block exits, and the session would be cut off mid-summary. Unless the
            # desk's own `end` already sealed it — a second hangup writes nothing at all.
            if not any(entry["type"] == "call.summary" for entry in heard):
                app_socket.send_json(a_frame("call.hangup", AGENT, {}, call=started["call"]))
                entry_until(app_socket, "call.summary", keeping=heard)


def sent(gateway: TestClient, call: str, said: dict[str, Any], bearer: str = A_KEY) -> Any:
    """One verb at the HTTP door, with whichever key this test is knocking with."""
    handle: Any = gateway
    return handle.post(
        f"/v1/calls/{call}/verbs", json=said, headers={"Authorization": f"Bearer {bearer}"}
    )


def _typed(heard: list[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    """Every entry of these types, in the order the log wrote them."""
    return [entry for entry in heard if entry["type"] in types]


# The states, the transcripts and the metrics of a turn are the text session's own suite
# (tests/api/calls/test_chat.py); a supervise test names the facts it is about and no others.
def _after(heard: list[dict[str, Any]], type: str, *keeping: str) -> list[dict[str, Any]]:
    """The entries of these types written after that one, in the order the log wrote them."""
    seen = [entry["type"] for entry in heard]
    rest = heard[seen.index(type) + 1 :]
    return [entry for entry in rest if entry["type"] in keeping]
