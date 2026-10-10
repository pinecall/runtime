"""What an agent runs: its declaration, the org's settings over it, the class's own over those."""

import dataclasses
from collections.abc import Mapping

from pinecall.domain.agent import AgentConfig, Lexicon, Model, Tuning, Voice
from pinecall.providers.build import Modality, doing, installed
from pinecall.providers.catalog import Stage

# Model ids carry slashes of their own (`openai/gpt-5` on LiveKit Inference): the first one ends
# the vendor.
SEPARATOR = "/"


def apply_tuning(
    declared: AgentConfig, tuning: Tuning, lexicon: Lexicon, *, defaults: Mapping[Modality, Stage]
) -> AgentConfig:
    """The declaration with the org's settings over it, and the class's own over those."""
    fixed = declared.fixed

    def kept[T](name: str, declared_value: T, set_value: T) -> T:
        return declared_value if name in fixed else set_value

    return dataclasses.replace(
        declared,
        greeting=kept("greeting", declared.greeting, tuning.greeting),
        language=kept(
            "language",
            declared.language,
            declared.language if tuning.language is None else tuning.language,
        ),
        voice=kept("voice", declared.voice, _voice(declared, tuning, defaults)),
        stt=kept("stt", declared.stt, _ears(declared, tuning, defaults)),
        llm=kept("llm", declared.llm, _thinking(declared, tuning, defaults)),
        hangup=kept("hangup", declared.hangup, tuning.hangup),
        turn=kept("turn", declared.turn, tuning.turn),
        memory=kept("memory", declared.memory, tuning.memory),
        record=kept(
            "record", declared.record, declared.record if tuning.record is None else tuning.record
        ),
        max_duration_s=(
            declared.max_duration_s if tuning.max_duration_s is None else tuning.max_duration_s
        ),
        llm_timeout_s=tuning.llm_timeout_s,
        knowledge=kept("knowledge", declared.knowledge, tuning.knowledge),
        bases=kept("docs", declared.bases, tuning.bases or ()),
        says=kept("says", declared.says, dict(lexicon.said)),
        hears=kept("hears", declared.hears, lexicon.heard),
    )


def model_of(text: str | None, modality: Modality, *, in_use: str) -> Model | None:
    """`vendor/model`, a vendor alone, or a model on the vendor in use; None when none is named."""
    if text is None:
        return None
    word = text.strip()
    vendor, slash, model = word.partition(SEPARATOR)
    if slash:
        return Model(provider=doing(vendor.lower(), modality), model=model)
    if word.lower() in installed():
        return Model(provider=doing(word.lower(), modality), model="")
    return Model(provider=doing(in_use, modality), model=word)


# `tts_model` names the model of the vendor `tts` names, or of the one in use.
def _voice(
    declared: AgentConfig, tuning: Tuning, defaults: Mapping[Modality, Stage]
) -> Voice | None:
    knobs = (tuning.tts, tuning.voice, tuning.tts_model, tuning.tts_builds, tuning.tts_options)
    if all(knob is None for knob in knobs):
        return None
    in_use = _in_use(declared.voice, defaults["tts"])
    named = model_of(tuning.tts, "tts", in_use=in_use)
    provider = in_use if named is None else named.provider
    model = tuning.tts_model or (None if named is None else named.model or None)
    return Voice(
        provider=provider,
        model=model,
        voice_id=tuning.voice,
        builds=tuning.tts_builds,
        options=dict(tuning.tts_options or {}),
    )


# A plugin's class or options, or a temperature, belong to one vendor: set on an agent that names
# none, they name the one in use, which runs then without the default's fallbacks.
def _thinking(
    declared: AgentConfig, tuning: Tuning, defaults: Mapping[Modality, Stage]
) -> Model | None:
    in_use = _in_use(declared.llm, defaults["llm"])
    named = model_of(tuning.llm, "llm", in_use=in_use)
    knobs = (tuning.temperature, tuning.llm_builds, tuning.llm_options)
    if all(knob is None for knob in knobs):
        return named
    return dataclasses.replace(
        named or Model(provider=in_use, model=""),
        temperature=tuning.temperature,
        builds=tuning.llm_builds,
        options=dict(tuning.llm_options or {}),
    )


def _ears(
    declared: AgentConfig, tuning: Tuning, defaults: Mapping[Modality, Stage]
) -> Model | None:
    in_use = _in_use(declared.stt, defaults["stt"])
    named = model_of(tuning.stt, "stt", in_use=in_use)
    if tuning.stt_builds is None and tuning.stt_options is None:
        return named
    return dataclasses.replace(
        named or Model(provider=in_use, model=""),
        builds=tuning.stt_builds,
        options=dict(tuning.stt_options or {}),
    )


def _in_use(declared: Model | Voice | None, default: Stage) -> str:
    return default.vendor if declared is None else declared.provider
