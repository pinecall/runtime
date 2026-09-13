"""A tool's read-back waits for its gap: it never cuts a sentence somebody is still hearing."""

from __future__ import annotations

from typing import Any

import pytest

from pinecall.session.voice.reading_back import read_back

pytestmark = pytest.mark.unit

IT_IS_DONE = "Reservado: el lunes a las nueve con el doctor Sáez."


class FakeSpeech:
    """A speech handle as `read_back` uses one: whether it is over, and who to tell when it is."""

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
    """A session that remembers what it was asked to say, and what was playing when."""

    def __init__(self, playing: FakeSpeech | None = None) -> None:
        self.current_speech = playing
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def test_with_nothing_playing_it_is_said_at_once() -> None:
    live = FakeSession(playing=None)

    read_back(live, IT_IS_DONE)

    assert live.said == [IT_IS_DONE]


def test_a_handle_that_is_already_over_is_not_waited_for() -> None:
    live = FakeSession(playing=FakeSpeech(over=True))

    read_back(live, IT_IS_DONE)

    assert live.said == [IT_IS_DONE]


def test_it_says_nothing_while_a_sentence_is_still_playing() -> None:
    """The whole point: `say()` can only CUT, so the read-back has to wait to be said at all."""
    playing = FakeSpeech()
    live = FakeSession(playing)

    read_back(live, IT_IS_DONE)

    assert live.said == [], "a caller is mid-sentence and hears no second voice over it"


def test_and_says_it_the_moment_that_sentence_ends() -> None:
    playing = FakeSpeech()
    live = FakeSession(playing)
    read_back(live, IT_IS_DONE)

    live.current_speech = None
    playing.ends()

    assert live.said == [IT_IS_DONE]


def test_a_gap_that_closed_before_it_got_there_is_waited_out_again() -> None:
    """The caller started the next turn while it waited. It lands at the first real silence, and
    on top of nobody — which is what the recursion inside the callback is for."""
    first, second = FakeSpeech(), FakeSpeech()
    live = FakeSession(first)
    read_back(live, IT_IS_DONE)

    live.current_speech = second
    first.ends()
    assert live.said == [], "the next sentence had already started"

    live.current_speech = None
    second.ends()
    assert live.said == [IT_IS_DONE]


def test_it_is_said_once_and_not_once_per_sentence_it_waited_through() -> None:
    first, second = FakeSpeech(), FakeSpeech()
    live = FakeSession(first)
    read_back(live, IT_IS_DONE)

    live.current_speech = second
    first.ends()
    live.current_speech = None
    second.ends()
    second.ends()

    assert live.said == [IT_IS_DONE]
