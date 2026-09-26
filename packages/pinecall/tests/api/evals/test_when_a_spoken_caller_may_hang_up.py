"""When a spoken run decides the agent has finished answering, and the turn that fooled it once."""

import time

import pytest

from pinecall.api.evals.agent_finished import (
    A_SILENT_OPENING_S,
    AN_ANSWER_MAY_TAKE_S,
    has_answer_landed,
    is_call_over,
    is_line_open,
    until_the_answer_lands,
)
from pinecall.log.entry import Entry
from pinecall.log.store.memory import MemoryStore

pytestmark = pytest.mark.unit


def _states(*said: str | tuple[str, str]) -> list[Entry]:
    """A log where an `agent.state` entry carries the state livekit published with it."""
    written: list[Entry] = []
    for seq, one in enumerate(said, 1):
        kind, data = one if isinstance(one, tuple) else (one, None)
        written.append(
            Entry(
                seq=seq,
                ts=float(seq),
                call="call_1",
                agent="clinica-norte",
                type=kind,
                ephemeral=False,
                data={"state": data} if data else {},
            )
        )
    return written


def _log(*types: str) -> list[Entry]:
    """A call's log as far as its types go; nothing here reads a field of one."""
    return [
        Entry(
            seq=seq,
            ts=float(seq),
            call="call_1",
            agent="clinica-norte",
            type=one,
            ephemeral=False,
            data={},
        )
        for seq, one in enumerate(types, 1)
    ]


def test_a_line_with_no_answer_yet_holds_the_line() -> None:
    assert has_answer_landed(_log("call.started", "turn.user"), said=1, since=0.0) is False


def test_the_agent_back_to_listening_after_the_line_is_the_signal() -> None:
    whole = _states("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))

    assert has_answer_landed(whole, said=1, since=0.0) is True


# 2026-09-11: the agent called freeSlots and then SAID "voy a consultar qué hay libre el lunes" —
# a filler, spoken after the tool had answered. A run watching for a turn after the tool hung up
# on it 234 ms later, in the middle of the generation that had the hours in it.
def test_a_filler_turn_after_the_tool_does_not_end_the_call() -> None:
    """The agent is still `thinking` when it has only announced what it is about to say."""
    filler = _states(
        "turn.user",
        ("agent.state", "thinking"),
        "tool.call",
        "tool.result",
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "thinking"),
    )

    assert has_answer_landed(filler, said=1, since=0.0) is False


def test_the_listening_that_came_before_the_caller_spoke_is_not_it() -> None:
    """Every call opens listening. That one is about the silence before the line, not after it."""
    opening = _states(("agent.state", "listening"), "turn.user", ("agent.state", "thinking"))

    assert has_answer_landed(opening, said=1, since=0.0) is False


def test_a_second_line_still_needs_its_own_answer() -> None:
    one = _states(
        "turn.user", ("agent.state", "speaking"), ("agent.state", "listening"), "turn.user"
    )

    assert has_answer_landed(one, said=2, since=0.0) is False


# The opening: the caller's first line waits for the greeting, never talks over it.
def test_an_agent_still_greeting_keeps_the_line_closed() -> None:
    log = _states(("agent.state", "listening"), ("agent.state", "speaking"))

    assert is_line_open(log, now=100.0) is False


def test_the_greeting_said_and_the_agent_listening_opens_the_line() -> None:
    log = _states(
        ("agent.state", "listening"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
    )

    assert is_line_open(log, now=5.0) is True


def test_an_agent_that_opens_with_nothing_is_believed_after_a_quiet_while() -> None:
    log = _states(("agent.state", "listening"))

    assert is_line_open(log, now=1.0 + A_SILENT_OPENING_S - 0.1) is False
    assert is_line_open(log, now=1.0 + A_SILENT_OPENING_S) is True


def test_no_agent_in_the_room_yet_keeps_the_line_closed() -> None:
    assert is_line_open(_log("call.started"), now=100.0) is False


# 2026-09-21, call_e6b08cd30647694ef0ac9b09: the caller stopped talking at 102.0s and was speaking
# again at 103.8s, over an answer the agent only finished at 104.8s — three of Sofia's turns came
# back cut to two words. The snapshot it was judged on ended BEFORE its own line: a `listening`
# left over from the turn before, which is a perfectly good answer to the question "has the agent
# finished?" as long as nobody asks which line it finished.
def test_a_log_that_has_not_caught_up_with_the_caller_says_nothing() -> None:
    """The previous turn's answer, read as this one's, is what puts a caller on top of the agent."""
    before = _states("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))

    assert has_answer_landed(before, said=1, since=0.0) is True
    # The caller fell silent after the last entry in that snapshot: it cannot be the answer.
    assert has_answer_landed(before, said=1, since=99.0) is False


# call_e64f46c28e1eafc76500cccf, 2026-09-21. The caller stopped at 145.2s and the agent had been
# listening since 140.2s — because it had finished answering the line BEFORE. The whole turn is in
# the snapshot, complete and consistent, and every bit of it belongs to the wrong line. The caller
# spoke again at 146.7s, half a second into the agent's answer; four of its turns in a row came
# back cut to three words. The log HAS moved on — `user.state` entries land while the caller is
# still being transcribed — so "has anything arrived since" cannot tell these apart. The line can.
def test_a_whole_answer_to_the_previous_line_is_not_an_answer_to_this_one() -> None:
    the_turn_before = _states(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
        "user.state",
    )

    # ts is the seq here, so the caller's only transcript sits at 1.0 and fell silent long after.
    assert has_answer_landed(the_turn_before, said=2, since=5.5) is False


# The same call, the other half: Flux ends a turn per sentence, so one spoken line arrives as two
# or three `turn.user` and `len(heard) >= said` is satisfied by the caller's own earlier sentences.
# What no split can fake is the agent LEAVING `listening` — it does that the moment the line is its.
def test_the_agent_must_have_been_handed_the_line_before_its_silence_counts() -> None:
    split = _states(
        "turn.user",
        ("agent.state", "thinking"),
        ("agent.state", "listening"),
        "turn.user",
        "turn.user",
    )

    assert has_answer_landed(split, said=2, since=0.0) is False


# A simulated caller runs in the gateway and the call it is on is the worker's, so the log is the
# only place it hears that somebody hung up. Before this, the console's Stop ended the call and the
# persona went on saying its remaining turns into an empty room.
def test_a_call_somebody_hung_up_is_over() -> None:
    assert is_call_over(_log("call.started", "turn.user", "call.ended")) is True


def test_a_call_still_being_spoken_on_is_not_over() -> None:
    assert is_call_over(_log("call.started", "turn.user", "turn.agent")) is False


async def test_the_wait_between_two_lines_ends_the_moment_the_call_does() -> None:
    """A hung-up call answers nothing: the line is not held the thirty seconds an answer may."""
    store = MemoryStore()
    for type_ in ("call.started", "turn.user", "call.ended"):
        await store.append("call_1", "clinica-norte", type_, {})

    began = time.monotonic()
    await until_the_answer_lands(store, "call_1", said=1)

    assert time.monotonic() - began < AN_ANSWER_MAY_TAKE_S / 2
