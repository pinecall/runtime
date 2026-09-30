"""Every installed plugin is a vendor, and one path builds any of them."""

import importlib.metadata
import re
import sys
from typing import Never

import pytest
from livekit.agents import llm, stt, tts

from pinecall.domain.agent import Tuning, Turn
from pinecall.domain.errors import DeclarationRefused, NotAvailable
from pinecall.domain.names import JsonObject
from pinecall.providers.build import (
    INFERENCE,
    Modality,
    Running,
    Vendor,
    completion_usage,
    installed,
    llm_of,
    plugin,
    primary,
    refuse_untaken,
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
    ears = stt_of(Running(acme, "k"), Turn(eot_threshold=0.8, min_interruption_words=2))
    assert isinstance(ears, AcmeSTT)
    assert ears.given["eot_threshold"] == 0.8
    assert "min_interruption_words" not in ears.given


def test_the_agents_endpointing_reaches_the_ears_as_the_silence_that_ends_a_turn(
    acme: str,
) -> None:
    ears = stt_of(Running(acme, "k"), Turn(endpointing_ms=700))
    assert isinstance(ears, AcmeSTT)
    assert ears.given["eot_timeout_ms"] == 700


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


def test_a_models_answer_is_counted_as_the_calls_usage_counts_it(acme: str) -> None:
    thinking = llm_of(Running(acme, "k", model="acme-2"))
    used = llm.CompletionUsage(
        completion_tokens=5, prompt_tokens=100, total_tokens=105, prompt_cached_tokens=40
    )
    counted = completion_usage(thinking, used)
    assert counted is not None
    assert (counted.model, counted.input_tokens, counted.input_cached_tokens) == (
        "acme-2",
        100,
        40,
    )
    assert counted.output_tokens == 5
    assert completion_usage(thinking, None) is None


def test_a_turn_knob_the_ears_take_under_no_name_is_refused_and_not_dropped(acme: str) -> None:
    ears, voice = Running(acme, "k"), Running(acme, "k")
    taken = Tuning(turn=Turn(endpointing_ms=700, eot_threshold=0.8, min_interruption_words=3))
    refuse_untaken(ears, voice, taken)
    with pytest.raises(DeclarationRefused, match="acme's stt takes no eager_eot_threshold"):
        refuse_untaken(ears, voice, Tuning(turn=Turn(eager_eot_threshold=0.5)))


def test_a_voice_the_voice_takes_under_no_name_is_refused_and_one_it_takes_passes(
    acme: str,
) -> None:
    refuse_untaken(Running(acme, "k"), Running(acme, "k"), Tuning(voice="v-7"))
    wordless = Running("deepgram", "k")
    with pytest.raises(DeclarationRefused, match="deepgram's tts takes no voice"):
        refuse_untaken(Running(acme, "k"), wordless, Tuning(voice="v-7"))
    refuse_untaken(Running(acme, "k"), wordless, Tuning())


def test_the_class_the_row_names_is_the_one_whose_knobs_are_read() -> None:
    flux = Running("deepgram", "k", builds="STTv2")
    nova = Running("deepgram", "k")
    eager = Tuning(turn=Turn(eot_threshold=0.8, eager_eot_threshold=0.5))
    refuse_untaken(flux, Running("cartesia", "k"), eager)
    with pytest.raises(DeclarationRefused, match="eot_threshold, eager_eot_threshold"):
        refuse_untaken(nova, Running("cartesia", "k"), eager)


def test_a_plugin_that_refuses_in_its_own_exception_is_refused_in_ours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # spitch takes no key argument and reads its own variable, so without it SpitchError.
    monkeypatch.delenv("SPITCH_API_KEY", raising=False)
    with pytest.raises(DeclarationRefused, match="spitch refused its STT"):
        stt_of(Running("spitch", "k"), None)


# ── every installed vendor, offline: what the runtime reads of livekit's classes ──


def _every_stage() -> list[tuple[str, Modality]]:
    return [
        (vendor.name, modality)
        for vendor in sorted(installed().values(), key=lambda found: found.name)
        if vendor.broken is None
        for modality in sorted(vendor.does)
    ]


# A key and nothing else, as an org that brought one: a vendor needs more (an endpoint, a
# model) only from the operator's row, and says so in our words, never in its own exception.
@pytest.mark.parametrize(("vendor", "modality"), _every_stage())
def test_every_installed_vendor_builds_offline_or_is_refused_in_our_words(
    vendor: str, modality: Modality, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SPITCH_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    built = _built_or_why(Running(vendor, "a-key"), modality)
    if isinstance(built, str):
        assert built.startswith(vendor), built
        return
    assert isinstance(built.model, str)
    assert isinstance(built.provider, str)
    if isinstance(built, llm.LLM):
        used = llm.CompletionUsage(completion_tokens=1, prompt_tokens=2, total_tokens=3)
        counted = completion_usage(built, used)
        assert counted is not None
        assert (counted.provider, counted.model) == (built.provider, built.model)
    elif isinstance(built, stt.STT):
        assert isinstance(built.capabilities, stt.STTCapabilities)
    else:
        assert isinstance(built.capabilities, tts.TTSCapabilities)
        assert built.sample_rate > 0


async def test_the_fake_transport_streams_a_reply_in_pieces(acme: str) -> None:
    script: JsonObject = {"replies": [["Hola", " Ana"]]}
    thinking = llm_of(Running(acme, "k", options=script))
    async with thinking.chat(chat_ctx=llm.ChatContext.empty()) as stream:
        pieces = [chunk.delta.content async for chunk in stream if chunk.delta]
    assert pieces == ["Hola", " Ana"]


async def test_a_stream_closed_before_its_first_piece_ends_without_an_error(acme: str) -> None:
    script: JsonObject = {"replies": [["tarde"]]}
    thinking = llm_of(Running(acme, "k", options=script))
    assert isinstance(thinking, AcmeLLM)
    thinking.thinks_s = 5.0
    stream = thinking.chat(chat_ctx=llm.ChatContext.empty())
    await stream.aclose()
    assert len(thinking.requests) == 1


def _built_or_why(
    running: Running, modality: Modality
) -> llm.LLM[Never] | stt.STT[Never] | tts.TTS[Never] | str:
    """The stage built, or the sentence it was refused with."""
    try:
        if modality == "llm":
            return llm_of(running)
        if modality == "stt":
            return stt_of(running, None)
        return tts_of(running)
    except DeclarationRefused as refused:
        return str(refused)
