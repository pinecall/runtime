"""Every installed plugin is a vendor, and one path builds any of them."""

import importlib.metadata
import re
import sys

import pytest

from pinecall.domain.agent import Turn
from pinecall.domain.errors import DeclarationRefused, NotAvailable
from pinecall.domain.names import JsonObject
from pinecall.providers.build import (
    INFERENCE,
    Running,
    Vendor,
    installed,
    llm_of,
    plugin,
    primary,
    stt_of,
    tts_of,
)
from tests.fakes.acme import AcmeContext, AcmeLLM, AcmeOptions, AcmeSTT, AcmeTTS
from tests.fakes.livekit import acme_plugin

# livekit-agents' extras that install no LLM, STT or TTS: avatars, a VAD, a turn detector, a
# tokenizer, a browser, a realtime model, and three that are not plugins. One livekit adds is in
# neither list until somebody reads what it installs.
NOT_A_VENDOR = frozenset(
    {
        "anam", "avatario", "avatartalk", "bey", "bithuman", "browser", "codecs", "did",
        "hamming", "images", "keyframe", "langchain", "lemonslice", "liveavatar", "mcp", "nltk",
        "phonic", "protoface", "runway", "silero", "simli", "spatius", "synthesia", "tavus",
        "trugen", "turn-detector", "ultravox",
    }
)  # fmt: skip


def _extras_we_install() -> set[str]:
    required = importlib.metadata.metadata("pinecall").get_all("Requires-Dist") or []
    return {
        extra
        for line in required
        if line.startswith("livekit-agents[")
        for extra in re.findall(r"\[([^\]]+)\]", line)[0].split(",")
    }


def test_every_extra_of_livekit_is_one_we_install_or_one_that_holds_no_vendor() -> None:
    offered = set(importlib.metadata.metadata("livekit-agents").get_all("Provides-Extra") or [])
    assert offered - _extras_we_install() - NOT_A_VENDOR == set()
    assert _extras_we_install() & NOT_A_VENDOR == set()


def test_the_vendors_are_the_forty_odd_plugins_installed_and_not_a_list_of_ours() -> None:
    vendors = installed()
    assert len(vendors) > 40
    assert vendors["deepgram"].does == {"stt", "tts"}
    assert vendors["anthropic"].does == {"llm"}
    assert "silero" not in vendors
    assert "turn_detector" not in vendors


def test_livekit_inference_is_a_vendor_of_the_three_stages() -> None:
    assert installed()[INFERENCE].does == {"llm", "stt", "tts"}


def test_a_plugin_that_does_not_import_is_listed_with_why_and_never_a_traceback() -> None:
    broken = [vendor for vendor in installed().values() if vendor.broken is not None]
    assert all(vendor.does == frozenset() and vendor.broken for vendor in broken)


def test_a_broken_plugin_is_refused_by_what_broke_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(installed(), "ghost", Vendor("ghost", frozenset(), broken="no socketio"))
    with pytest.raises(NotAvailable, match="the ghost plugin is installed and does not import"):
        plugin("ghost")


def test_a_vendor_nobody_installed_names_the_command_that_adds_it() -> None:
    with pytest.raises(DeclarationRefused, match=re.escape('"livekit-agents[nobody]"')):
        tts_of(Running("nobody", "a-key"))


def test_the_key_the_voice_and_the_language_land_under_the_names_the_plugin_gave_them(
    acme: str,
) -> None:
    speech = tts_of(Running(acme, "a-key", voice="v-7", language="es-ES"))
    assert isinstance(speech, AcmeTTS)
    assert speech.given == {
        "speech_key": "a-key",
        "voice_name": "v-7",
        "language_code": "es",
        "model": "acme-voice",
    }


def test_credentials_of_more_than_a_key_reach_the_constructor_as_they_are(acme: str) -> None:
    thinking = llm_of(Running(acme, {"api_key": "a-key", "temperature": 0.2}))
    assert isinstance(thinking, AcmeLLM)
    assert thinking.given == {"api_key": "a-key", "model": "acme-1", "temperature": 0.2}


def test_the_model_asked_for_wins_and_none_leaves_the_plugins_own(acme: str) -> None:
    params = llm_of(Running(acme, "k", model="acme-2"))
    left = llm_of(Running(acme, "k"))
    assert isinstance(params, AcmeLLM)
    assert isinstance(left, AcmeLLM)
    assert (params.given["model"], left.given["model"]) == ("acme-2", "acme-1")


def test_a_plugin_that_names_no_voice_is_simply_not_sent_one(acme: str) -> None:
    ears = stt_of(Running(acme, "k", voice="v-7"), None)
    assert isinstance(ears, AcmeSTT)
    assert "voice" not in ears.given


def test_the_languages_the_ears_listen_for_go_where_the_plugin_takes_them(acme: str) -> None:
    ears = stt_of(Running(acme, "k", language="es", hints=("es", "en")), None)
    assert isinstance(ears, AcmeSTT)
    assert (ears.given["language"], ears.given["language_hints"]) == ("es", ["es", "en"])


def test_the_agents_turn_reaches_the_ears_that_take_it_and_is_dropped_where_they_do_not(
    acme: str,
) -> None:
    ears = stt_of(Running(acme, "k"), Turn(eot_threshold=0.8, endpointing_ms=700))
    assert isinstance(ears, AcmeSTT)
    assert ears.given["eot_threshold"] == 0.8
    assert "endpointing_ms" not in ears.given


def test_an_object_the_plugin_takes_whole_is_built_from_the_rows_mapping_however_deep(
    acme: str,
) -> None:
    options: JsonObject = {"params": {"sensitivity": 0.3, "context": {"terms": ["Pinecall"]}}}
    ears = stt_of(Running(acme, "k", options=options), None)
    assert isinstance(ears, AcmeSTT)
    assert ears.given["params"] == AcmeOptions(sensitivity=0.3, context=AcmeContext(["Pinecall"]))


def test_the_class_the_row_names_is_built_and_one_the_plugin_lacks_is_refused(acme: str) -> None:
    assert isinstance(stt_of(Running(acme, "k", builds="OtherSTT"), None), AcmeSTT)
    with pytest.raises(DeclarationRefused, match="acme exports no STT named 'STTv9'"):
        stt_of(Running(acme, "k", builds="STTv9"), None)


def test_a_class_of_another_stage_is_refused_rather_than_built(acme: str) -> None:
    with pytest.raises(DeclarationRefused, match="exports no TTS named 'LLM'"):
        tts_of(Running(acme, "k", builds="LLM"))


def test_a_vendor_that_refuses_its_arguments_is_refused_before_the_call(acme: str) -> None:
    with pytest.raises(DeclarationRefused, match=r"acme refused its TTS: .*'loudness'"):
        tts_of(Running(acme, "k", options={"loudness": 11}))


def test_a_real_plugin_is_built_by_the_same_path_with_the_class_the_row_names() -> None:
    options: JsonObject = {"eot_timeout_ms": 1000}
    running = Running("deepgram", "k", model="flux-general-multi", builds="STTv2", options=options)
    ears = stt_of(running, Turn(eot_threshold=0.8))
    assert type(ears).__name__ == "STTv2"
    assert ears.model == "flux-general-multi"


def test_livekit_inference_is_built_on_the_boxs_pair_with_a_model_that_names_its_vendor() -> None:
    pair: JsonObject = {"api_key": "a-key", "api_secret": "a secret of thirty-two bytes or more"}
    thinking = llm_of(Running(INFERENCE, pair, model="openai/gpt-5-mini"))
    assert thinking.model == "openai/gpt-5-mini"


def test_every_spelling_of_a_language_is_its_base_code() -> None:
    assert [primary(item) for item in ("es-ES", "spanish", "es_MX", "EN-us", "en")] == [
        "es",
        "es",
        "es",
        "en",
        "en",
    ]


def test_no_language_is_none_and_not_an_empty_word() -> None:
    assert (primary(None), primary(""), primary("  ")) == (None, None, None)


def test_a_knob_the_agent_set_wins_over_the_rows_option_of_the_same_name(acme: str) -> None:
    ears = stt_of(Running(acme, "k", options={"eot_threshold": 0.5}), Turn(eot_threshold=0.9))
    assert isinstance(ears, AcmeSTT)
    assert ears.given["eot_threshold"] == 0.9


def test_an_agent_that_declared_no_turn_leaves_the_plugin_at_its_own(acme: str) -> None:
    ears = stt_of(Running(acme, "k"), None)
    assert isinstance(ears, AcmeSTT)
    assert ears.given["eot_threshold"] is None


def test_a_voice_nobody_chose_is_not_sent_and_the_plugin_speaks_with_its_own(acme: str) -> None:
    speech = tts_of(Running(acme, "k"))
    assert isinstance(speech, AcmeTTS)
    assert speech.given["voice_name"] is None


def test_a_plugin_that_did_not_import_is_tried_again_and_found_when_it_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(installed(), "acme", Vendor("acme", frozenset(), broken="no socketio"))
    monkeypatch.setitem(sys.modules, "livekit.plugins.acme", acme_plugin())
    assert plugin("acme").__name__ == "livekit.plugins.acme"
    assert installed()["acme"].does == {"llm", "stt", "tts"}
