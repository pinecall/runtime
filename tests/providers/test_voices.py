"""The voices a plugin lists, and a line spoken outside a call, both through the call's path."""

import io
import wave

import pytest

from pinecall.domain.errors import NotFound, UpstreamFailed
from pinecall.domain.names import JsonObject
from pinecall.providers.build import Running
from pinecall.providers.voices import sample, voices
from tests.fakes.acme import A_RATE


async def test_a_plugin_that_lists_its_voices_is_read_row_by_row(acme: str) -> None:
    listed: JsonObject = {
        "voices": [
            {"id": "v-1", "name": "Ana", "category": "premade"},
            {"voice_id": "v-2", "display_name": "Bo"},
        ]
    }
    found = await voices(Running(acme, "k", options=listed))
    assert [(item.id, item.name) for item in found] == [("v-1", "Ana"), ("v-2", "Bo")]
    assert found[0].detail == {"id": "v-1", "name": "Ana", "category": "premade"}


async def test_a_row_with_no_id_is_passed_over_and_the_rest_still_read(acme: str) -> None:
    listed: JsonObject = {"voices": [{"nothing": "here"}, {"id": "v-3"}]}
    found = await voices(Running(acme, "k", options=listed))
    assert [(item.id, item.name) for item in found] == [("v-3", "v-3")]


async def test_a_plugin_that_lists_nothing_is_refused_by_name() -> None:
    with pytest.raises(NotFound, match="deepgram lists no voices here"):
        await voices(Running("deepgram", "k"))


async def test_a_line_is_spoken_as_wav_at_the_voices_own_rate(acme: str) -> None:
    spoken = await sample(Running(acme, "k"), "Hola, gracias por llamar.")
    with wave.open(io.BytesIO(spoken.wav)) as heard:
        assert (heard.getframerate(), heard.getnchannels(), heard.getsampwidth()) == (A_RATE, 1, 2)
        assert heard.getnframes() > 0
    assert spoken.total_ms >= spoken.first_audio_ms >= 0


async def test_a_vendor_that_answers_with_no_audio_is_a_refusal_and_not_a_mute_wav(
    acme: str,
) -> None:
    with pytest.raises(UpstreamFailed, match="acme did not say it: no audio frames"):
        await sample(Running(acme, "k", options={"silent": True}), "Hola.")


async def test_a_streaming_vendor_is_asked_over_its_stream_as_a_call_speaks(acme: str) -> None:
    spoken = await sample(Running(acme, "k", builds="StreamedTTS"), "Hola. Gracias por llamar.")
    with wave.open(io.BytesIO(spoken.wav)) as heard:
        assert heard.getnframes() > 0


async def test_a_vendor_that_says_no_is_named_with_its_status_and_one_that_hangs_without(
    acme: str,
) -> None:
    refusing = Running(acme, "k", options={"refusal": "status"})
    with pytest.raises(UpstreamFailed, match=r"acme did not say it \(402\): no credit"):
        await sample(refusing, "Hola.")
    hanging = Running(acme, "k", options={"refusal": "connection"})
    with pytest.raises(UpstreamFailed, match="acme did not say it: no answer"):
        await sample(hanging, "Hola.")
    with pytest.raises(UpstreamFailed, match=r"acme did not list its voices \(402\)"):
        await voices(refusing)
