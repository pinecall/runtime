"""A held line: the melody, the ears the agent loses, and the entry that states both flags."""

from __future__ import annotations

from typing import Any

import pytest

from pinecall.session.voice import commands
from pinecall.session.voice.attention import Attending
from pinecall.session.voice.hold_melody import HoldMusic
from pinecall.session.voice.log_writer import Writing
from pinecall.session.voice.on_hold import Line
from pinecall_protocol import Command
from tests.session.voice.fakes import CALL, Recording, ScriptedSession
from tests.session.voice.test_commands import End, Prompt, Recorded

pytestmark = pytest.mark.unit


class Held:
    """One call's line, and the log it writes to."""

    def __init__(self) -> None:
        self.live = ScriptedSession()
        self.recording = Recording()
        self.writing = Writing(self.recording, CALL)
        self.writing.open()
        self.line = Line(self.live, self.writing, HoldMusic)  # pyright: ignore[reportArgumentType]
        self.attending = Attending(self.line, self.writing)

    async def applied(self, type: str, data: dict[str, Any] | None = None) -> None:
        """One verb of the line through the dispatch table."""
        applying = commands.Applying(
            self.live,  # pyright: ignore[reportArgumentType]
            Prompt(),
            End(),
            Recorded(),
            None,
            None,
            self.line,
            self.attending,
        )
        command = Command(type=type, agent="clinica-norte", call=CALL, data=data or {})
        await commands.apply(applying, command)
        await self.writing.flushed()


async def test_a_held_call_leaves_the_agent_neither_speaking_nor_hearing() -> None:
    held = Held()
    await held.applied("call.hold")
    assert (held.live.output.enabled, held.live.input.enabled) == (False, False)
    assert held.live.interruptions == 1
    (line,) = held.recording.of("call.line")
    assert (line.data["held"], line.data["muted"]) == (True, False)


async def test_taking_the_call_off_hold_gives_the_agent_its_ears_before_its_voice() -> None:
    held = Held()
    await held.applied("call.hold")
    await held.applied("call.unhold")
    assert (held.live.output.enabled, held.live.input.enabled) == (True, True)
    assert [line.data["held"] for line in held.recording.of("call.line")] == [True, False]


async def test_holding_a_call_that_is_already_held_says_nothing_twice() -> None:
    held = Held()
    await held.applied("call.hold")
    await held.applied("call.hold")
    assert len(held.recording.of("call.line")) == 1
