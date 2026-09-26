"""The hold melody around a tool: after a grace, once for tools side by side, never in the way."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pinecall.session.hold_melody import DEFAULT
from pinecall.session.voice import hold_melody
from pinecall.session.voice.hold_melody import Floor, HoldMusic

pytestmark = pytest.mark.unit


class Handle:
    """What livekit's player hands back for one sound: stopped, or still playing."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class Player:
    """livekit's BackgroundAudioPlayer, as far as the melody uses it."""

    def __init__(self) -> None:
        self.played: list[tuple[Any, bool]] = []
        self.handles: list[Handle] = []

    def play(self, config: Any, *, loop: bool) -> Handle:
        self.played.append((config, loop))
        self.handles.append(Handle())
        return self.handles[-1]

    async def aclose(self) -> None:
        return None


def a_melody(player: Player, source: Path = DEFAULT, floor: Floor | None = None) -> HoldMusic:
    music = HoldMusic(source, floor)
    music._player = player  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue] — the room is livekit's to give
    return music


# Read here because the fixture below shortens it for every test in this file.
DECLARED_GRACE_S = hold_melody.GRACE_S


@pytest.fixture(autouse=True)
def no_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hold_melody, "GRACE_S", 0.01)


async def test_a_tool_that_takes_a_while_plays_the_melody_looped_and_stops_it_after() -> None:
    player = Player()
    music = a_melody(player)
    async with music.playing():
        await asyncio.sleep(0.05)
    [(config, loop)] = player.played
    assert (config.source, loop) == (str(DEFAULT), True)
    assert 0 < config.volume < 1, "under the voice that comes back"
    assert player.handles[0].stopped


async def test_a_tool_that_answers_inside_the_grace_plays_nothing() -> None:
    player = Player()
    music = a_melody(player)
    async with music.playing():
        pass
    await asyncio.sleep(0.05)
    assert player.played == []


async def test_tools_side_by_side_play_it_once_until_the_last_one_ends() -> None:
    player = Player()
    music = a_melody(player)

    async def a_tool(seconds: float) -> None:
        async with music.playing():
            await asyncio.sleep(seconds)

    first = asyncio.create_task(a_tool(0.05))
    second = asyncio.create_task(a_tool(0.1))
    await first
    assert len(player.played) == 1
    assert not player.handles[0].stopped, "the second tool is still running"
    await second
    assert player.handles[0].stopped


async def test_a_call_with_no_room_or_turned_off_plays_nothing_and_the_tool_still_runs() -> None:
    for music in (
        HoldMusic(),
        await HoldMusic.in_this_room(DEFAULT),
        await HoldMusic.in_this_room(None),
    ):
        ran = False
        async with music.playing():
            ran = True
        assert ran


# The grace is not a comfort setting: the agent ANNOUNCES the tools that take a while — "let me
# look that up" — and starts the tool in the same breath, so a melody on a short grace comes up
# underneath the agent's own voice and the caller hears both at once (2026-09-21, in production).
def test_the_grace_outlasts_the_line_the_agent_says_before_the_tool() -> None:
    assert DECLARED_GRACE_S >= 2.0


# The tool starts while the agent is still saying the line that announced it — the model emits its
# text and its tool call in one response, so they overlap by construction. On a timer alone the
# melody therefore came up underneath the agent's own voice every time a tool ran after an
# announcement, which is the one thing it exists to avoid.
async def test_the_melody_waits_for_the_agent_to_stop_talking() -> None:
    player = Player()
    floor = Floor()
    floor.changed("speaking")
    music = a_melody(player, DEFAULT, floor)

    async with music.playing():
        await asyncio.sleep(0.10)
        assert player.played == [], "it started under the agent's own voice"
        floor.changed("listening")
        await asyncio.sleep(0.20)
        assert player.played != [], "and it never started once the agent had stopped"
