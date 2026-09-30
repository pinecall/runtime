"""The vendors livekit ships once installed, and one of them built for a stage of a call."""

import dataclasses
import importlib
import importlib.util
import inspect
import logging
import pkgutil
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from types import ModuleType
from typing import Literal, Never

from livekit.agents import llm, stt, tts
from livekit.agents.language import LanguageCode
from typing_extensions import TypeIs

from pinecall.domain.agent import Turn
from pinecall.domain.errors import DeclarationRefused, NotAvailable
from pinecall.domain.names import Credentials, Json, JsonObject
from pinecall.wire.metrics import LLMModelUsage

type Modality = Literal["llm", "stt", "tts"]

# A call's stage as it is built: the plugin's object, or livekit's adapter over several.
type Thinking = llm.LLM[Never] | llm.FallbackAdapter

type Ears = stt.STT[Never] | stt.FallbackAdapter

type Speaking = tts.TTS[Never] | tts.FallbackAdapter

# The two local end-of-turn models, both read off the audio on the worker's CPU: livekit's own
# (its detector's small version) and Daily's Smart Turn v3 (smart-turn-livekit).
type TurnModel = Literal["v1-mini", "smart-turn-v3"]


logger = logging.getLogger(__name__)


# LiveKit Inference: the same three classes, on the box's LiveKit key pair, models `vendor/model`.
INFERENCE = "livekit"


_INFERENCE_MODULE = "livekit.agents.inference"


_PLUGINS = "livekit.plugins"


# livekit's three bases are generic in the extra events a subclass may emit; a plugin adds none.
_AN_LLM: type[llm.LLM[Never]] = llm.LLM


_AN_STT: type[stt.STT[Never]] = stt.STT


_A_TTS: type[tts.TTS[Never]] = tts.TTS


# Plugins spell the same argument differently; the first one a constructor declares gets it.
# `voice_id` comes before `voice`: a plugin that declares both reads the id.
_THE_KEY = ("api_key", "speech_key", "secret")


_THE_VOICE = ("voice_id", "voice_uuid", "voice", "speaker", "voice_name")


_THE_LANGUAGE = ("language", "language_code")


_THE_HINTS = ("language_hint", "language_hints")


# livekit's adapter wraps ears that do not stream with a VAD the session does not hold, and refuses
# voices of another channel count: such a fallback is left out and the call runs on the rest.
LEFT_OUT = "%s is left out of the stage's fallbacks: %s"


@dataclass(frozen=True)
class Vendor:
    """A vendor this process can build: what it does, or why its plugin does not import."""

    name: str
    does: frozenset[Modality]
    broken: str | None = None


@dataclass(frozen=True)
class Running:
    """One stage of a call as it runs: vendor, model, key, and what the plugin is told."""

    vendor: str
    credentials: Credentials
    # None: the plugin's own default.
    model: str | None = None
    # The plugin's class, where the operator names one other than LLM, STT or TTS.
    builds: str | None = None
    # The operator's keyword arguments for this vendor and stage, as the plugin names them.
    options: JsonObject = field(default_factory=dict[str, Json])
    voice: str | None = None
    language: str | None = None
    # Languages the ears listen for, the call's first.
    hints: tuple[str, ...] = ()
    # The ears end the turn themselves, so the session stacks no detector on top.
    ends_the_turn: bool = False
    # Which local model reads the end of the turn off the audio, where the ears do not.
    turn_model: TurnModel = "v1-mini"
    # On the box's key rather than the org's own.
    lent: bool = False
    # Who takes over, in order, each on its own key: the stage is livekit's FallbackAdapter.
    fallbacks: tuple["Running", ...] = ()


MODALITIES: tuple[Modality, ...] = ("llm", "stt", "tts")


CLASS_OF: dict[Modality, str] = {"llm": "LLM", "stt": "STT", "tts": "TTS"}


@cache
def installed() -> dict[str, Vendor]:
    """Every vendor of this process: each livekit plugin exporting an LLM, an STT or a TTS."""
    found = {INFERENCE: _vendor(INFERENCE, _INFERENCE_MODULE)}
    spec = importlib.util.find_spec(_PLUGINS)
    locations = spec.submodule_search_locations if spec is not None else None
    for module in pkgutil.iter_modules(locations or []):
        vendor = _vendor(module.name, f"{_PLUGINS}.{module.name}")
        if vendor.does or vendor.broken:
            found[vendor.name] = vendor
    return found


def doing(vendor: str, modality: Modality) -> str:
    """The vendor's name, once it is installed and does that stage; DeclarationRefused if not."""
    found = installed().get(vendor)
    if found is None:
        raise DeclarationRefused(
            f"no vendor named {vendor!r} is installed here; GET /v1/providers lists every one"
        )
    if found.broken is None and modality not in found.does:
        raise DeclarationRefused(
            f"{vendor} has no {modality}: it does {', '.join(sorted(found.does))}"
        )
    return vendor


# A plugin that did not import is tried again: what broke it (a package missing) may have been
# fixed since the process started.
def plugin(vendor: str) -> ModuleType:
    """The installed module of a vendor; refused when it is not installed or does not import."""
    found = installed().get(vendor)
    if found is None:
        raise DeclarationRefused(
            f"no vendor named {vendor!r} is installed; "
            f'`pip install "livekit-agents[{vendor}]"` adds its plugin'
        )
    module = _INFERENCE_MODULE if vendor == INFERENCE else f"{_PLUGINS}.{vendor}"
    if found.broken is not None:
        found = _vendor(vendor, module)
        installed()[vendor] = found
    if found.broken is not None:
        raise NotAvailable(f"the {vendor} plugin is installed and does not import: {found.broken}")
    return importlib.import_module(module)


# What a plugin or a row hands back is typed as nothing; these say what it is, once.
def a_mapping(value: object) -> TypeIs[Mapping[str, object]]:
    """Whether the value is a mapping, read with its keys as names."""
    return isinstance(value, Mapping)


def a_list(value: object) -> TypeIs[list[object]]:
    """Whether the value is a list."""
    return isinstance(value, list)


def primary(language: str | None) -> str | None:
    """The base code of a language (`es` for `es-ES` or `spanish`); vendors refuse full tags."""
    if language is None or not language.strip():
        return None
    return LanguageCode(language.strip()).language


def llm_of(running: Running) -> llm.LLM[Never]:
    """The LLM a stage runs: the plugin's class called with the key, the model and the options."""
    return _built("llm", _AN_LLM, running, {})


# A model the runtime asks itself, outside a call's session: livekit meters none of it.
def completion_usage(
    model: llm.LLM[Never], used: llm.CompletionUsage | None
) -> LLMModelUsage | None:
    """What one answer of a model cost in tokens, as the call's usage writes it."""
    if used is None:
        return None
    return LLMModelUsage(
        provider=model.provider,
        model=model.model,
        input_tokens=used.prompt_tokens,
        input_cached_tokens=used.prompt_cached_tokens,
        input_cache_creation_tokens=used.cache_creation_tokens,
        output_tokens=used.completion_tokens,
    )


def tts_of(running: Running) -> tts.TTS[Never]:
    """The voice a stage speaks with, its voice and language under the names its plugin uses."""
    return _built("tts", _A_TTS, running, {})


def stt_of(running: Running, turn: Turn | None) -> stt.STT[Never]:
    """The ears a stage hears with; the agent's turn knobs reach them where they take them."""
    return _built("stt", _AN_STT, running, _turn_knobs(turn))


# A call's three stages: with no fallback, exactly the plugin's object; with some, livekit's
# FallbackAdapter over the stage and the vendors that take over, in order.
def thinking_of(running: Running) -> Thinking:
    """The LLM a call thinks with, over its fallbacks when the stage names any."""
    primary = llm_of(running)
    if not running.fallbacks:
        return primary
    return llm.FallbackAdapter([primary, *(llm_of(fallback) for fallback in running.fallbacks)])


def ears_of(running: Running, turn: Turn | None) -> Ears:
    """The ears a call hears with, over its fallbacks when the stage names any that stream."""
    primary = stt_of(running, turn)
    if not primary.capabilities.streaming:
        return primary
    backups: list[stt.STT[Never]] = []
    for fallback in running.fallbacks:
        ears = stt_of(fallback, turn)
        if ears.capabilities.streaming:
            backups.append(ears)
        else:
            logger.warning(LEFT_OUT, ears.label, "it does not stream")
    return stt.FallbackAdapter([primary, *backups]) if backups else primary


# The adapter resamples to the highest rate among them, but mixes no channels.
def speaking_of(running: Running) -> Speaking:
    """The voice a call speaks with, over its fallbacks when the stage names any that fit."""
    primary = tts_of(running)
    backups: list[tts.TTS[Never]] = []
    for fallback in running.fallbacks:
        voice = tts_of(fallback)
        if voice.num_channels == primary.num_channels:
            backups.append(voice)
        else:
            logger.warning(LEFT_OUT, voice.label, "its channels differ")
    return tts.FallbackAdapter([primary, *backups]) if backups else primary


def _turn_knobs(turn: Turn | None) -> dict[str, object]:
    knobs: dict[str, object] = {} if turn is None else dataclasses.asdict(turn)
    given = {knob: value for knob, value in knobs.items() if value is not None}
    # The agent's endpointing is the silence that closes a turn: the ears that take it call it so.
    if turn is not None and turn.endpointing_ms is not None:
        given["eot_timeout_ms"] = turn.endpointing_ms
    return given


def _vendor(name: str, module: str) -> Vendor:
    try:
        imported = importlib.import_module(module)
    except ImportError as broken:
        logger.warning("the %s plugin does not import, so it builds nothing", name, exc_info=True)
        return Vendor(name, frozenset(), broken=str(broken))
    return Vendor(name, frozenset(m for m in MODALITIES if hasattr(imported, CLASS_OF[m])))


def _built[T](
    modality: Modality, base: type[T], running: Running, knobs: Mapping[str, object]
) -> T:
    name = running.builds or CLASS_OF[modality]
    made: object = getattr(plugin(running.vendor), name, None)
    if not (isinstance(made, type) and issubclass(made, base)):
        raise DeclarationRefused(f"{running.vendor} exports no {CLASS_OF[modality]} named {name!r}")
    constructor: Callable[..., T] = made
    accepts = _parameters(made)
    given: dict[str, object] = {**running.options}
    given |= {knob: value for knob, value in knobs.items() if knob in accepts}
    given |= _credentials(running.credentials, accepts)
    given |= _first(accepts, _THE_VOICE, running.voice)
    # A row that names the language wins: a vendor may want the full tag the call cuts to its base.
    if not any(name in running.options for name in _THE_LANGUAGE):
        given |= _first(accepts, _THE_LANGUAGE, primary(running.language))
    given |= _first(accepts, _THE_HINTS, list(running.hints) or None)
    if running.model is not None:
        given["model"] = running.model
    try:
        return constructor(**_shaped(made, given))
    except (TypeError, ValueError) as refused:
        raise DeclarationRefused(f"{running.vendor} refused its {name}: {refused}") from refused


def _parameters(made: type) -> frozenset[str]:
    try:
        return frozenset(inspect.signature(made).parameters)
    except (TypeError, ValueError):
        return frozenset()


def _credentials(credentials: Credentials, accepts: frozenset[str]) -> dict[str, object]:
    if isinstance(credentials, dict):
        return dict(credentials)
    return _first(accepts, _THE_KEY, credentials)


def _first(accepts: frozenset[str], names: tuple[str, ...], value: object) -> dict[str, object]:
    chosen = next((name for name in names if name in accepts), None)
    return {} if chosen is None or value is None else {chosen: value}


# A plugin that takes its options as one object (`params=STTOptions(...)`) is given that object,
# built from the row's mapping field by field, however deep.
def _shaped(made: type, given: Mapping[str, object]) -> dict[str, object]:
    try:
        declared = typing.get_type_hints(made.__init__)
    except (NameError, TypeError):
        return dict(given)
    return {name: _as_declared(declared.get(name), value) for name, value in given.items()}


def _as_declared(declared: object, value: object) -> object:
    shape = _dataclass_in(declared)
    if shape is None or not a_mapping(value):
        return value
    typed = typing.get_type_hints(shape)
    return shape(**{name: _as_declared(typed.get(name), given) for name, given in value.items()})


def _dataclass_in(declared: object) -> type | None:
    if isinstance(declared, type) and dataclasses.is_dataclass(declared):
        return declared
    if isinstance(declared, types.UnionType):
        return next(
            (
                declared_one
                for declared_one in typing.get_args(declared)
                if _dataclass_in(declared_one)
            ),
            None,
        )
    return None
