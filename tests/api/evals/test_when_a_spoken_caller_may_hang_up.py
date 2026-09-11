"""When a spoken run decides the agent has finished answering, and the turn that fooled it once."""

import pytest

from pinecall.api.evals.spoken import _the_answer_has_landed
from pinecall.log.entry import Entry

pytestmark = pytest.mark.unit


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
    assert _the_answer_has_landed(_log("call.started", "turn.user"), said=1) is False


def test_one_answer_for_one_line_is_enough_when_no_tool_ran() -> None:
    assert _the_answer_has_landed(_log("turn.user", "turn.agent"), said=1) is True


# 2026-09-11, `en-el-chat-ofrece-mas-de-dos-horas` spoken: the agent called freeSlots and said "voy
# a consultar qué hay libre el lunes". One turn for one line — the count held, the caller hung up,
# and the hours it had gone to fetch were never read. The judge then said the agent never said
# 'nueve', which was true and was this function's fault.
def test_a_turn_that_only_announces_a_tool_does_not_end_the_call() -> None:
    """The answer is the turn AFTER the tool answers, and the caller has to wait for it."""
    announced = _log("turn.user", "turn.agent", "tool.call", "tool.result")

    assert _the_answer_has_landed(announced, said=1) is False


def test_the_turn_after_the_tool_is_the_answer() -> None:
    whole = _log("turn.user", "turn.agent", "tool.call", "tool.result", "turn.agent")

    assert _the_answer_has_landed(whole, said=1) is True


def test_a_second_line_still_needs_its_own_answer() -> None:
    one = _log("turn.user", "turn.agent", "tool.call", "tool.result", "turn.agent", "turn.user")

    assert _the_answer_has_landed(one, said=2) is False
