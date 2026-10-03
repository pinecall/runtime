"""Tests for a call's time: its ceiling, the warning before it, a quiet chat in a room ended."""

import asyncio
import time

import pytest
from livekit.agents import llm
from livekit.agents.voice.events import ConversationItemAddedEvent

from pinecall.log.store import Store
from pinecall.session import clock
from pinecall.session.call import CLOSING
from pinecall.session.session import Session
from pinecall.wire.events import CreditsExhausted
from tests.conftest import postgres
from tests.session.conftest import Box, a_session, kinds, model_of
from tests.session.test_session import A_SUPERVISOR, NOBODY


@postgres
async def test_a_limit_under_two_minutes_is_told_at_its_half_and_ends_at_the_limit(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["Vamos cerrando"])
    await session.start()
    await clock.keep_time(session, 1, exhausted=None)
    await session.close()
    params = model_of(session).requests
    assert CLOSING in str([getattr(item, "text_content", "") for item in params[0].items])
    ended = next(entry for entry in await store.whole(call) if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("timeout", "platform")


@postgres
async def test_a_person_on_the_line_is_not_talked_over_and_the_limit_still_holds(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["nunca"])
    await session.start()
    session.call.taken_by = A_SUPERVISOR
    await clock.keep_time(session, 1, exhausted=None)
    await session.close()
    assert model_of(session).requests == []
    assert "call.ended" in await kinds(store, call)


@postgres
async def test_the_orgs_minutes_ending_the_call_first_are_written_before_it_ends(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    spent = CreditsExhausted(org="org_1", quota="minutes", used=30, limit=30)
    await clock.keep_time(session, 1, exhausted=spent)
    await session.close()
    written = await kinds(store, call)
    assert written.index("credits.exhausted") < written.index("call.ended")


async def test_no_limit_keeps_no_clock() -> None:
    started = time.monotonic()
    await clock.keep_time(object.__new__(Session), 0, exhausted=None)
    await clock.end_when_quiet(object.__new__(Session), 0)
    assert time.monotonic() - started < 0.1


# A chat in a room has no ceiling: a visitor who leaves the page open would hold the seat for ever.
@postgres
async def test_a_chat_left_quiet_ends_as_a_timeout_and_a_message_keeps_it(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    quiet = asyncio.create_task(clock.end_when_quiet(session, 0.3))
    await asyncio.sleep(0.2)
    message = llm.ChatMessage(role="user", content=["hola"])
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=message))
    await asyncio.sleep(0.2)
    assert session.ended is None
    await asyncio.sleep(0.2)
    assert session.ended == ("timeout", "platform")
    await quiet
    await session.close()
    ended = next(entry for entry in await store.whole(call) if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("timeout", "platform")


@postgres
async def test_the_agent_is_told_a_minute_before_a_limit_of_two_minutes_or_more(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    real = asyncio.sleep

    async def counted(delay: float) -> None:
        slept.append(delay)
        await real(0)

    session = a_session(box, NOBODY, ["cerramos"])
    await session.start()
    monkeypatch.setattr(asyncio, "sleep", counted)
    await clock.keep_time(session, 300, exhausted=None)
    monkeypatch.undo()
    await session.close()
    assert slept[:2] == [240, 60]
