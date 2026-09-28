"""What an agent runs as its org set it: the declaration, the tuning and the lexicon over it."""

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
    """The declaration with the org's settings over it: what the world set is what runs."""
    return dataclasses.replace(
        declared,
        greeting=tuning.greeting,
        voice=_voice(declared, tuning, defaults),
        stt=model_of(tuning.stt, "stt", in_use=_in_use(declared.stt, defaults["stt"])),
        llm=model_of(tuning.llm, "llm", in_use=_in_use(declared.llm, defaults["llm"])),
        hangup=tuning.hangup,
        turn=tuning.turn,
        memory=tuning.memory,
        record=declared.record if tuning.record is None else tuning.record,
        max_duration_s=(
            declared.max_duration_s if tuning.max_duration_s is None else tuning.max_duration_s
        ),
        knowledge=tuning.knowledge,
        bases=tuning.bases or (),
        says=dict(lexicon.said),
        hears=lexicon.heard,
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
    if tuning.tts is None and tuning.voice is None and tuning.tts_model is None:
        return None
    in_use = _in_use(declared.voice, defaults["tts"])
    named = model_of(tuning.tts, "tts", in_use=in_use)
    provider = in_use if named is None else named.provider
    model = tuning.tts_model or (None if named is None else named.model or None)
    return Voice(provider=provider, model=model, voice_id=tuning.voice)


def _in_use(declared: Model | Voice | None, default: Stage) -> str:
    return default.vendor if declared is None else declared.provider
