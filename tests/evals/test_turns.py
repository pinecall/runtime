"""Tests for when the caller may speak: the agent's answer off the log, and the turns on it."""

import asyncio
import time

import pytest

from pinecall.domain.names import JsonObject
from pinecall.evals import turns
from pinecall.evals.turns import (
    A_SILENT_OPENING_S,
    answered,
    has_answer_landed,
    is_agent_busy,
    is_call_over,
    is_line_open,
)
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.wire.frames import Entry
from tests.conftest import postgres

LINES = ("Esa me viene bien.", "Sí, confírmemela.")


def a_log(*written: str | tuple[str, str] | tuple[str, JsonObject]) -> list[Entry]:
    """A log of these kinds in order, one a second: a state by its name, a tool by its call id."""
    entries: list[Entry] = []
    for seq, line in enumerate(written, 1):
        kind, given = line if isinstance(line, tuple) else (line, None)
        data: JsonObject = {"state": given} if isinstance(given, str) else given or {}
        entries.append(
            Entry(
                seq=seq,
                ts=float(seq),
                call="call_1",
                agent="clinica-norte",
                type=kind,
                ephemeral=False,
                data=data,
            )
        )
    return entries


# ── when the caller may speak ──


def test_a_line_with_no_answer_yet_holds_the_line() -> None:
    assert not has_answer_landed(a_log("call.started", "turn.user"), 1, since=0.0)


def test_the_agent_having_spoken_and_back_to_listening_is_the_signal() -> None:
    log = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
    )
    assert has_answer_landed(log, 1, since=0.0)


# The disaster of 2026-10-10: an agent that looked something up twice was talked over between
# the two, since livekit publishes `listening` while a tool runs and between one and the next.
def test_an_agent_listening_between_one_tool_and_the_next_has_not_answered() -> None:
    between = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("tool.call", {"call_id": "t1"}),
        ("agent.state", "listening"),
        ("tool.result", {"call_id": "t1"}),
    )
    assert not has_answer_landed(between, 1, since=0.0)
    second = [*between, *a_log(("tool.call", {"call_id": "t2"}))[0:1]]
    assert not has_answer_landed(second, 1, since=0.0)
    answered_both = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("tool.call", {"call_id": "t1"}),
        ("agent.state", "listening"),
        ("tool.result", {"call_id": "t1"}),
        ("tool.call", {"call_id": "t2"}),
        ("tool.result", {"call_id": "t2"}),
        ("agent.state", "thinking"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
    )
    assert has_answer_landed(answered_both, 1, since=0.0)


def test_an_agent_that_thought_and_said_nothing_yet_has_not_answered() -> None:
    quiet = a_log("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))
    assert not has_answer_landed(quiet, 1, since=0.0)


def test_the_agent_is_busy_while_it_thinks_or_speaks_or_a_tool_of_its_runs() -> None:
    assert is_agent_busy(a_log("turn.user", ("agent.state", "thinking")))
    assert is_agent_busy(a_log("turn.user", ("agent.state", "speaking")))
    assert is_agent_busy(
        a_log("turn.user", ("tool.call", {"call_id": "t1"}), ("agent.state", "listening"))
    )
    assert not is_agent_busy(a_log("turn.user", ("agent.state", "listening")))


def test_a_filler_turn_after_the_tool_does_not_end_the_wait() -> None:
    filler = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        "tool.call",
        "tool.result",
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "thinking"),
    )
    assert not has_answer_landed(filler, 1, since=0.0)


def test_a_tool_still_running_is_the_agent_mid_turn_even_while_it_listens() -> None:
    running = a_log(
        "turn.user", ("agent.state", "thinking"), "tool.call", ("agent.state", "listening")
    )
    assert not has_answer_landed(running, 1, since=0.0)


def test_the_listening_that_came_before_the_caller_spoke_is_not_it() -> None:
    opening = a_log(("agent.state", "listening"), "turn.user", ("agent.state", "thinking"))
    assert not has_answer_landed(opening, 1, since=0.0)


def test_a_second_line_still_needs_its_own_answer() -> None:
    log = a_log("turn.user", ("agent.state", "speaking"), ("agent.state", "listening"), "turn.user")
    assert not has_answer_landed(log, 2, since=0.0)


def test_an_agent_still_greeting_keeps_the_line_closed() -> None:
    log = a_log(("agent.state", "listening"), ("agent.state", "speaking"))
    assert not is_line_open(log, now=100.0)


def test_the_greeting_said_and_the_agent_listening_opens_the_line() -> None:
    log = a_log(
        ("agent.state", "listening"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
    )
    assert is_line_open(log, now=5.0)


def test_an_agent_that_opens_with_nothing_is_believed_after_a_quiet_while() -> None:
    log = a_log(("agent.state", "listening"))
    assert not is_line_open(log, now=1.0 + A_SILENT_OPENING_S - 0.1)
    assert is_line_open(log, now=1.0 + A_SILENT_OPENING_S)


def test_no_agent_in_the_room_yet_keeps_the_line_closed() -> None:
    assert not is_line_open(a_log("call.started"), now=100.0)


# A snapshot may predate the caller's line: its `listening` would be the turn before's.
def test_a_log_that_has_not_caught_up_with_the_caller_says_nothing() -> None:
    before = a_log(
        "turn.user", ("agent.state", "speaking"), "turn.agent", ("agent.state", "listening")
    )
    assert has_answer_landed(before, 1, since=0.0)
    assert not has_answer_landed(before, 1, since=99.0)


def test_a_whole_answer_to_the_previous_line_is_not_an_answer_to_this_one() -> None:
    before = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
        "user.state",
    )
    assert not has_answer_landed(before, 2, since=5.5)


# Some ears end a turn per sentence: only the agent leaving `listening` proves it took the line.
def test_the_agent_must_have_been_handed_the_line_before_its_silence_counts() -> None:
    split = a_log(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "listening"),
        "turn.user",
        "turn.user",
    )
    assert not has_answer_landed(split, 2, since=0.0)


def test_a_call_somebody_hung_up_is_over() -> None:
    assert is_call_over(a_log("call.started", "turn.user", "call.ended"))


def test_a_call_still_being_spoken_on_is_not_over() -> None:
    assert not is_call_over(a_log("call.started", "turn.user", "turn.agent"))


@postgres
async def test_the_wait_between_two_lines_ends_the_moment_the_call_does(store: Store) -> None:
    logs = Logs(store)
    log = logs.writing("call_1", "clinica-norte")
    await log.append("call.started", {})
    await log.append("turn.user", {})
    await log.append("call.ended", {})
    began = time.monotonic()
    await answered(logs, "call_1", 1)
    assert time.monotonic() - began < 5.0


# A long answer is spoken for as long as it takes: the caller never talks over it. An agent that
# listens in silence with no answer is given up on, once the silence has lasted.
@postgres
async def test_the_caller_waits_through_a_long_answer_and_gives_up_on_silence_alone(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(turns, "A_SILENCE_MAY_LAST_S", 0.6)
    monkeypatch.setattr(turns, "A_BEAT_S", 0.1)
    logs = Logs(store)
    log = logs.writing("call_1", "clinica-norte")
    await log.append("call.started", {})
    # The caller said its line; the agent hears it a moment after the wait begins, as on a call.
    waiting = asyncio.create_task(answered(logs, "call_1", 1))
    await asyncio.sleep(0.05)
    await log.append("turn.user", {"text": "hola"})
    await log.append("agent.state", {"state": "speaking"})
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(waiting), 1.5)
    await log.append("agent.state", {"state": "listening"})
    began = time.monotonic()
    await waiting
    assert 0.5 <= time.monotonic() - began < 3.0


# The answer holds only once the quiet after it has passed: the next tool lands a moment later.
@postgres
async def test_an_answer_holds_after_the_quiet_and_not_before(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(turns, "A_QUIET_AFTER_S", 0.5)
    monkeypatch.setattr(turns, "A_BEAT_S", 0.1)
    logs = Logs(store)
    log = logs.writing("call_1", "clinica-norte")
    await log.append("call.started", {})
    waiting = asyncio.create_task(answered(logs, "call_1", 1))
    await asyncio.sleep(0.05)
    await log.append("turn.user", {"text": "hola"})
    await log.append("agent.state", {"state": "speaking"})
    await log.append("turn.agent", {"text": "buenas"})
    await log.append("agent.state", {"state": "listening"})
    began = time.monotonic()
    await waiting
    assert 0.4 <= time.monotonic() - began < 3.0
