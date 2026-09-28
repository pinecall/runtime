"""Hold music."""

import asyncio
from pathlib import Path

import pytest

from pinecall.session import hold as hold_module
from pinecall.session.hold import HoldMusic
from tests.fakes.livekit import Player


async def test_tools_side_by_side_share_one_melody_until_the_last_ends() -> None:
    melody = HoldMusic(Path(__file__))
    melody.began()
    melody.began()
    melody.ended()
    assert melody.running == 1
    melody.ended()
    assert melody.running == 0


async def test_a_tool_that_takes_a_while_plays_the_melody_looped_and_stops_it_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 0.01)
    melody = HoldMusic(Path("hold.ogg"))
    melody.player = Player()
    melody.began()
    await asyncio.sleep(0.05)
    assert melody.player.played == [("hold.ogg", True)]
    melody.ended()
    assert melody.player.handles[0].done()


async def test_the_melody_waits_for_the_agent_to_stop_talking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 0.01)
    melody = HoldMusic(Path("hold.ogg"))
    player = Player()
    melody.player = player
    melody.floor(speaking=True)
    melody.began()
    await asyncio.sleep(0.05)
    assert player.played == []
    melody.floor(speaking=False)
    await asyncio.sleep(0.05)
    assert player.played == [("hold.ogg", True)]
    melody.ended()
