"""Hold music."""

import asyncio
import io
import math
import struct
import wave
from pathlib import Path

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.session import hold as hold_module
from pinecall.session.hold import HoldMusic, container_of, converted
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


async def test_the_melody_keeps_when_it_sounded_for_the_recording_and_nothing_inside_the_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 0.01)
    melody = HoldMusic(Path("hold.ogg"))
    melody.player = Player()
    melody.began()
    melody.ended()
    assert melody.played == []
    melody.began()
    await asyncio.sleep(0.05)
    melody.ended()
    ((start, end),) = melody.played
    assert 0 < end - start < 1


async def test_a_melody_still_playing_when_the_call_ends_counts_up_to_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 0.01)
    melody = HoldMusic(Path("hold.ogg"))
    melody.player = Player()
    melody.began()
    await asyncio.sleep(0.05)
    assert melody.played == []
    ((start, end),) = melody.sounded()
    assert 0 < end - start < 1


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


def a_wav(seconds: float, *, rate: int = 16_000) -> bytes:
    """A sine of that length, mono, as a person would upload it."""
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(at / 10)))
                for at in range(int(rate * seconds))
            )
        )
    return out.getvalue()


def test_an_upload_comes_out_as_ogg_opus_at_the_rate_livekit_mixes() -> None:
    clip = converted(a_wav(2.0))
    assert clip.audio[:4] == b"OggS"
    assert clip.seconds == 2.0


def test_what_is_no_audio_and_what_is_too_short_are_refused_in_the_persons_words() -> None:
    with pytest.raises(DeclarationRefused, match="no audio this box can read"):
        converted(b"not a melody")
    with pytest.raises(DeclarationRefused, match="stutter"):
        converted(a_wav(0.3))


# The container is named by the file's own bytes and FFmpeg is told it: nothing is probed, so a
# playlist or a concat script, which FFmpeg would otherwise follow, is no audio here.
def test_the_container_is_read_off_the_bytes_and_a_script_is_no_audio() -> None:
    assert container_of(a_wav(1.0)) == "wav"
    assert container_of(b"OggS" + bytes(40)) == "ogg"
    assert container_of(b"ID3" + bytes(40)) == "mp3"
    assert container_of(bytes(4) + b"ftypM4A " + bytes(32)) == "mov,mp4,m4a,3gp,3g2,mj2"
    playlist = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:1,\nhttp://169.254.169.254/x\n"
    script = b"ffconcat version 1.0\nfile recordings/call_1/audio.ogg\n"
    for scripted in (playlist, script):
        assert container_of(scripted) is None
        with pytest.raises(DeclarationRefused, match="no audio this box can read"):
            converted(scripted)
