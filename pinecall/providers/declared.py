"""What an agent runs as its org set it: the declaration, the tuning and the lexicon over it."""

import dataclasses
from collections.abc import Mapping

from pinecall.domain.types import AgentConfig, Lexicon, Model, Tuning, Voice
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
        stt=_model(tuning.stt, "stt", in_use=_in_use(declared.stt, defaults["stt"])),
        llm=_model(tuning.llm, "llm", in_use=_in_use(declared.llm, defaults["llm"])),
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


def model_of(said: str, modality: Modality, *, in_use: str) -> Model:
    """`vendor/model`, a vendor alone (its default model), or a model on the vendor in use."""
    vendor, model = vendor_and_model(said, modality, in_use=in_use)
    return Model(provider=vendor, model=model or "")


def voice_of(tts: str | None, voice: str | None, *, in_use: str) -> Voice | None:
    """The voice a tuning or a persona names: `tts` moves the vendor, `voice` is its id."""
    if tts is None and voice is None:
        return None
    vendor, model = (in_use, None) if tts is None else vendor_and_model(tts, "tts", in_use=in_use)
    return Voice(provider=vendor, model=model, voice_id=voice)


def vendor_and_model(said: str, modality: Modality, *, in_use: str) -> tuple[str, str | None]:
    """Split a knob into its vendor and its model; the vendor must be installed and do the stage."""
    word = said.strip()
    vendor, slash, model = word.partition(SEPARATOR)
    if slash:
        return doing(vendor.lower(), modality), model
    if word.lower() in installed():
        return doing(word.lower(), modality), None
    return doing(in_use, modality), word


# `tts_model` names the model of the vendor `tts` names, or of the one in use.
def _voice(
    declared: AgentConfig, tuning: Tuning, defaults: Mapping[Modality, Stage]
) -> Voice | None:
    in_use = _in_use(declared.voice, defaults["tts"])
    voice = voice_of(tuning.tts, tuning.voice, in_use=in_use)
    if tuning.tts_model is None:
        return voice
    base = voice or Voice(provider=in_use)
    return dataclasses.replace(base, model=tuning.tts_model)


def _model(said: str | None, modality: Modality, *, in_use: str) -> Model | None:
    return None if said is None else model_of(said, modality, in_use=in_use)


def _in_use(declared: Model | Voice | None, default: Stage) -> str:
    return default.vendor if declared is None else declared.provider
