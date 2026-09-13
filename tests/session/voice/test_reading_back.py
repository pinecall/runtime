"""A tool's read-back is queued at once, so it lands before the reply the model has yet to write."""

from __future__ import annotations

from typing import Any

import pytest

from pinecall.session.voice.reading_back import read_back

pytestmark = pytest.mark.unit

IT_IS_DONE = "Reservado: el lunes a las nueve con el doctor Sáez."
THE_PREAMBLE = "Perfecto, le confirmo el lunes a las nueve."
THE_REPLY = "Su cita está confirmada. ¿Alguna cosa más?"


class FakeSpeech:
    """A speech handle as a session hands one out: whether it is over, and who to tell then."""

    def __init__(self, over: bool = False) -> None:
        self._over = over
        self._waiting: list[Any] = []

    def done(self) -> bool:
        return self._over

    def add_done_callback(self, callback: Any) -> None:
        self._waiting.append(callback)

    def ends(self) -> None:
        """The sentence finishes, however it finished, and everyone waiting is told."""
        self._over = True
        for callback in list(self._waiting):
            callback(self)


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


def test_it_is_queued_while_the_preamble_is_still_being_spoken() -> None:
    """The bug this file is about: waiting for the gap put the receipt after the whole reply."""
    live = FakeSession(playing=FakeSpeech(over=False))

    read_back(live, IT_IS_DONE)

    assert live.said == [IT_IS_DONE], "a receipt that waits for a gap is a receipt said too late"


def test_it_lands_before_the_reply_the_model_had_not_written_yet() -> None:
    """Preamble, receipt, reply: the model's account of the result is queued after this one."""
    playing = FakeSpeech(over=False)
    live = FakeSession(playing=playing)

    read_back(live, IT_IS_DONE)
    live.say(THE_REPLY)  # the model, once it has seen the tool's answer

    assert live.said == [IT_IS_DONE, THE_REPLY]


def test_it_is_said_once_per_tool() -> None:
    """One receipt per tool that ran. A read-back heard twice was its own bug, once."""
    live = FakeSession(playing=FakeSpeech(over=False))

    read_back(live, IT_IS_DONE)

    assert live.said.count(IT_IS_DONE) == 1


def test_a_speech_that_ends_afterwards_says_nothing_more() -> None:
    """Nothing is left subscribed to the line: the receipt is spent when it is queued."""
    playing = FakeSpeech(over=False)
    live = FakeSession(playing=playing)

    read_back(live, IT_IS_DONE)
    playing.ends()

    assert live.said == [IT_IS_DONE]
