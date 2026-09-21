"""When a spoken run decides the agent has finished answering, and the turn that fooled it once."""

import pytest

from pinecall.api.evals.listening import A_SILENT_OPENING_S, the_answer_has_landed, the_line_is_open
from pinecall.log.entry import Entry

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
    assert the_answer_has_landed(_log("call.started", "turn.user"), said=1, since=0.0) is False


def test_the_agent_back_to_listening_after_the_line_is_the_signal() -> None:
    whole = _states("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))

    assert the_answer_has_landed(whole, said=1, since=0.0) is True


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

    assert the_answer_has_landed(filler, said=1, since=0.0) is False


def test_the_listening_that_came_before_the_caller_spoke_is_not_it() -> None:
    """Every call opens listening. That one is about the silence before the line, not after it."""
    opening = _states(("agent.state", "listening"), "turn.user", ("agent.state", "thinking"))

    assert the_answer_has_landed(opening, said=1, since=0.0) is False


def test_a_second_line_still_needs_its_own_answer() -> None:
    one = _states(
        "turn.user", ("agent.state", "speaking"), ("agent.state", "listening"), "turn.user"
    )

    assert the_answer_has_landed(one, said=2, since=0.0) is False


# The opening: the caller's first line waits for the greeting, never talks over it.
def test_an_agent_still_greeting_keeps_the_line_closed() -> None:
    log = _states(("agent.state", "listening"), ("agent.state", "speaking"))

    assert the_line_is_open(log, now=100.0) is False


def test_the_greeting_said_and_the_agent_listening_opens_the_line() -> None:
    log = _states(
        ("agent.state", "listening"),
        ("agent.state", "speaking"),
        "turn.agent",
        ("agent.state", "listening"),
    )

    assert the_line_is_open(log, now=5.0) is True


def test_an_agent_that_opens_with_nothing_is_believed_after_a_quiet_while() -> None:
    log = _states(("agent.state", "listening"))

    assert the_line_is_open(log, now=1.0 + A_SILENT_OPENING_S - 0.1) is False
    assert the_line_is_open(log, now=1.0 + A_SILENT_OPENING_S) is True


def test_no_agent_in_the_room_yet_keeps_the_line_closed() -> None:
    assert the_line_is_open(_log("call.started"), now=100.0) is False


# 2026-09-21, call_e6b08cd30647694ef0ac9b09: the caller stopped talking at 102.0s and was speaking
# again at 103.8s, over an answer the agent only finished at 104.8s — three of Sofia's turns came
# back cut to two words. The snapshot it was judged on ended BEFORE its own line: a `listening`
# left over from the turn before, which is a perfectly good answer to the question "has the agent
# finished?" as long as nobody asks which line it finished.
def test_a_log_that_has_not_caught_up_with_the_caller_says_nothing() -> None:
    """The previous turn's answer, read as this one's, is what puts a caller on top of the agent."""
    before = _states("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))

    assert the_answer_has_landed(before, said=1, since=0.0) is True
    # The caller fell silent after the last entry in that snapshot: it cannot be the answer.
    assert the_answer_has_landed(before, said=1, since=99.0) is False


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

    assert the_answer_has_landed(split, said=2, since=0.0) is False
