"""A tool's read-back is said from inside the tool, and heard out before the tool returns."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.session.voice.reading_back import read_back

pytestmark = pytest.mark.unit

IT_IS_DONE = "Reservado: el lunes a las nueve con el doctor Sáez."
THE_REPLY = "Su cita está confirmada. ¿Alguna cosa más?"


class FakeHandle:
    """A speech handle as say() hands one out: awaiting it is waiting for the audio to play."""

    def __init__(self, session: FakeSession, text: str) -> None:
        self._session = session
        self._text = text

    def __await__(self):
        async def played() -> None:
            await asyncio.sleep(0)
            self._session.history.append(self._text)

        return played().__await__()


class FakeSession:
    """A session that remembers what it was asked to say, and what has reached its history."""

    def __init__(self) -> None:
        self.said: list[str] = []
        # What the model would read: a sentence joins only once its audio has played.
        self.history: list[str] = []

    def say(self, text: str) -> FakeHandle:
        self.said.append(text)
        return FakeHandle(self, text)


async def test_it_is_said_at_once_and_does_not_wait_for_the_line_to_go_quiet() -> None:
    """Waiting for a gap was the bug: by the time one came, the model had queued its whole reply
    and the receipt spoke after the agent had already said "¿Alguna cosa más?"."""
    live = FakeSession()

    await read_back(live, IT_IS_DONE)

    assert live.said == [IT_IS_DONE]


async def test_it_is_in_the_history_before_the_tool_returns() -> None:
    """The reply to a tool result is generated the instant the tool returns, from the history as it
    stands. A receipt not yet in it was answered as though never said: the booking, twice."""
    live = FakeSession()

    await read_back(live, IT_IS_DONE)

    assert live.history == [IT_IS_DONE]


async def test_it_lands_before_the_reply_the_model_has_yet_to_write() -> None:
    """Said from inside the tool, the model is still waiting for it: preamble, receipt, reply."""
    live = FakeSession()

    await read_back(live, IT_IS_DONE)
    await live.say(THE_REPLY)  # the model, once this tool has returned

    assert live.said == [IT_IS_DONE, THE_REPLY]
    assert live.history == [IT_IS_DONE, THE_REPLY]


async def test_it_is_said_once() -> None:
    """One receipt per tool that ran. A read-back heard twice was its own bug, once."""
    live = FakeSession()

    await read_back(live, IT_IS_DONE)

    assert live.said.count(IT_IS_DONE) == 1
