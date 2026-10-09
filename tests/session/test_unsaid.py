"""Tests for the lexicon read back: the transcript says the written word, not its pronunciation."""

from collections.abc import AsyncIterator

from livekit.agents.types import TimedString

from pinecall.session._unsaid import unsaid

SAYS = {"Pinecall": "pain-col", "DKV": "de ka uve"}


async def pieces(*said: str | TimedString) -> AsyncIterator[str | TimedString]:
    for piece in said:
        yield piece


async def heard(*said: str | TimedString) -> list[str | TimedString]:
    return [piece async for piece in unsaid(pieces(*said), SAYS)]


async def test_a_spoken_form_in_one_word_is_the_word_written_with_its_timing() -> None:
    back = await heard(
        TimedString("I'm ", 0.0, 0.2),
        TimedString("pain-col's ", 0.2, 0.7),
        TimedString("agent.", 0.7, 1.0),
    )
    assert "".join(back) == "I'm Pinecall's agent."
    assert isinstance(back[1], TimedString)
    assert (back[1].start_time, back[1].end_time) == (0.2, 0.7)


async def test_a_spoken_form_across_words_is_one_piece_from_the_first_to_the_last() -> None:
    back = await heard(
        TimedString("Your ", 0.0, 0.3),
        TimedString("de ", 0.3, 0.5),
        TimedString("ka ", 0.5, 0.6),
        TimedString("uve ", 0.6, 0.9),
        TimedString("policy.", 0.9, 1.4),
    )
    assert "".join(back) == "Your DKV policy."
    joined = next(piece for piece in back if "DKV" in piece)
    assert isinstance(joined, TimedString)
    assert (joined.start_time, joined.end_time) == (0.3, 0.9)


async def test_words_that_only_start_like_a_spoken_form_pass_as_they_came() -> None:
    back = await heard("The ", "pain ", "is ", "gone.")
    assert back == ["The ", "pain ", "is ", "gone."]


async def test_an_agent_with_no_lexicon_passes_every_piece_untouched() -> None:
    passed = [piece async for piece in unsaid(pieces("Pinecall ", "pain-col"), {})]
    assert passed == ["Pinecall ", "pain-col"]
