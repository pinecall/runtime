"""A tool's read-back is said from inside the tool, before the model has written about it."""

from __future__ import annotations

import pytest

from pinecall.session.voice.reading_back import read_back

pytestmark = pytest.mark.unit

IT_IS_DONE = "Reservado: el lunes a las nueve con el doctor Sáez."
THE_REPLY = "Su cita está confirmada. ¿Alguna cosa más?"


class FakeSpeech:
    """A speech handle as a session hands one out: whether it is over, and who to tell then."""

    def __init__(self, over: bool = False) -> None:
        self._over = over

    def done(self) -> bool:
        return self._over


class FakeSession:
    """A session that remembers what it was asked to say, in the order it was asked."""

    def __init__(self, playing: FakeSpeech | None = None) -> None:
        self.current_speech = playing
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def test_with_nothing_playing_it_is_said_at_once() -> None:
    live = FakeSession(playing=None)

    read_back(live, IT_IS_DONE)

    assert live.said == [IT_IS_DONE]


def test_it_does_not_wait_for_the_line_to_go_quiet() -> None:
    """Waiting for a gap was the bug: by the time one came, the model had queued its whole reply
    and the receipt spoke after the agent had already said "¿Alguna cosa más?"."""
    live = FakeSession(playing=FakeSpeech(over=False))

    read_back(live, IT_IS_DONE)

    assert live.said == [IT_IS_DONE]


def test_it_lands_before_the_reply_the_model_has_yet_to_write() -> None:
    """Said from inside the tool, the model is still waiting for it: preamble, receipt, reply."""
    live = FakeSession(playing=FakeSpeech(over=False))

    read_back(live, IT_IS_DONE)
    live.say(THE_REPLY)  # the model, once this tool has returned

    assert live.said == [IT_IS_DONE, THE_REPLY]


def test_it_is_said_once() -> None:
    """One receipt per tool that ran. A read-back heard twice was its own bug, once."""
    live = FakeSession(playing=FakeSpeech(over=False))

    read_back(live, IT_IS_DONE)

    assert live.said.count(IT_IS_DONE) == 1
