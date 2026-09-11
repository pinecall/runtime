"""When a spoken run decides the agent has finished answering, and the turn that fooled it once."""

import pytest

from pinecall.api.evals.spoken import the_answer_has_landed
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
    assert the_answer_has_landed(_log("call.started", "turn.user"), said=1) is False


def test_the_agent_back_to_listening_after_the_line_is_the_signal() -> None:
    whole = _states("turn.user", ("agent.state", "thinking"), ("agent.state", "listening"))

    assert the_answer_has_landed(whole, said=1) is True


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

    assert the_answer_has_landed(filler, said=1) is False


def test_the_listening_that_came_before_the_caller_spoke_is_not_it() -> None:
    """Every call opens listening. That one is about the silence before the line, not after it."""
    opening = _states(("agent.state", "listening"), "turn.user", ("agent.state", "thinking"))

    assert the_answer_has_landed(opening, said=1) is False


def test_a_second_line_still_needs_its_own_answer() -> None:
    one = _states(
        "turn.user", ("agent.state", "speaking"), ("agent.state", "listening"), "turn.user"
    )

    assert the_answer_has_landed(one, said=2) is False
