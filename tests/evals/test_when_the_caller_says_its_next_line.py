"""What the simulated caller waits for between two of its own lines, and what it used to guess."""

import pytest

from pinecall.evals.calling import Settled, every_turn

pytestmark = pytest.mark.unit

LINES = ("Esa me viene bien.", "Sí, confírmemela.")


class _Said:
    """A mouth that records instead of speaking, and the golden's lines handed out in order."""

    def __init__(self, lines: tuple[str, ...] = LINES) -> None:
        self.out: list[str] = []
        self._lines = lines
        self._at = 0

    async def say(self, text: str) -> None:
        self.out.append(text)

    async def next_line(self, _left: int) -> tuple[str, bool]:
        if self._at >= len(self._lines):
            return "", False
        line, self._at = self._lines[self._at], self._at + 1
        return line, False


class _Waited:
    """The `settled` a run hands in: it records how many lines it was told had been said."""

    def __init__(self) -> None:
        self.after: list[int] = []

    async def __call__(self, so_far: int) -> None:
        self.after.append(so_far)


async def _turns(said: _Said, settled: Settled) -> int:
    """`every_turn` with a mouth that writes down what it said; the room is never touched."""
    return await every_turn(said, len(LINES), said.next_line, settled)


# Until this waited for the agent, the caller slept six fixed seconds between lines — and a turn
# that runs a tool takes thirteen, because the model announces it, the voice says that, the tool
# runs and the voice says the answer. `reserva-cuando-el-paciente-dice-que-si` was heard once out
# of twice on every spoken run: its second line went out over the answer to its first.
async def test_the_caller_waits_for_the_answer_to_each_line_before_saying_the_next() -> None:
    """And it is told how many it has said, so the wait knows what it is waiting for."""
    said, waited = _Said(), _Waited()

    spoken = await _turns(said, waited)

    assert (spoken, said.out) == (2, list(LINES))
    assert waited.after == [1, 2]


async def test_a_line_the_golden_does_not_have_ends_the_call_without_a_wait() -> None:
    """Nothing to answer means nothing to wait for: the run stops asking."""
    said, waited = _Said(lines=(LINES[0],)), _Waited()

    spoken = await every_turn(said, 2, said.next_line, waited)

    assert (spoken, waited.after) == (1, [1])
