"""The vendors livekit ships once installed, and one of them built for a stage of a call."""

import asyncio
import dataclasses
import importlib
import importlib.util
import inspect
import logging
import pkgutil
import re
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from types import ModuleType
from typing import Any, Literal, Never, override

from livekit.agents import llm, stt, tts
from livekit.agents.language import LanguageCode
from livekit.agents.llm import Tool, ToolChoice
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given
from typing_extensions import TypeIs

from pinecall.domain.agent import Tuning, Turn
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


_NO_SUCH_CLASS = "{vendor} exports no {stage} named {name!r}"


# What an org's credentials object may carry: the fields the vendor's constructors take, none of
# them an address, a file or a session. Where a vendor is reached is the operator's, in the
# providers row's tuning; a tenant's object that named one would aim the box's requests anywhere.
NOT_A_CREDENTIAL = (
    "{vendor} credentials carry {field!r}: an address, a file or a session is the operator's to "
    "set in the providers row, not a credential"
)


NOT_TAKEN = "{vendor} takes no credential named {field!r}; its plugin takes {fields}"


NOT_A_VALUE = (
    "{vendor} credentials: {field!r} is a string, a number or a boolean of at most 1024 characters"
)


TOO_MANY_FIELDS = "{vendor} credentials carry {count} fields: eight at most"


_NOT_A_CREDENTIAL = frozenset(
    {"url", "host", "endpoint", "session", "client", "transport", "connection", "model"}
)


_NOT_A_CREDENTIAL_ENDS = ("_url", "_base", "_endpoint", "_host", "_file", "_path", "_session")


_MOST_FIELDS = 8


_LONGEST_VALUE = 1024


_UNTAKEN = (
    "{vendor}'s {stage} takes no {knobs}, so a call would run without it: leave it out, or pick a "
    "vendor that takes it"
)


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
    # What every request of an llm stage carries beside the conversation, in the vendor's names.
    request: JsonObject = field(default_factory=dict[str, Json])
    # On the box's key rather than the org's own.
    lent: bool = False
    # Who takes over, in order, each on its own key: the stage is livekit's FallbackAdapter.
    fallbacks: tuple["Running", ...] = ()


# A plugin's constructor takes no field for every request (a model's thinking, its effort), and
# livekit hands each request extra_kwargs: the row's ride every one, a caller's own over them.
class LLMWithRequest(llm.LLM[Never]):
    """A plugin's LLM whose every request also carries the operator's fields for its model."""

    def __init__(self, built: llm.LLM[Never], request: JsonObject) -> None:
        """Wrap the built LLM, and pass on what it reports as its own."""
        super().__init__()
        self.built = built
        self.request = request
        self._label = built.label
        built.on("metrics_collected", self._reported_metrics)  # pyright: ignore[reportUnknownMemberType]
        built.on("error", self._reported_error)  # pyright: ignore[reportUnknownMemberType]

    @property
    @override
    def model(self) -> str:
        """The built LLM's model, so usage and metrics name it."""
        return self.built.model

    @property
    @override
    def provider(self) -> str:
        """The built LLM's vendor, so usage and metrics name it."""
        return self.built.provider

    @override
    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        """The built LLM's request, with the row's fields and the caller's over them."""
        given = extra_kwargs if is_given(extra_kwargs) else {}
        return self.built.chat(
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs={**self.request, **given},
        )

    @override
    def prewarm(self, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Open the built LLM's connection before the first request."""
        self.built.prewarm(loop=loop)

    @override
    async def aclose(self) -> None:
        """Stop listening to the built LLM, and close it."""
        self.built.off("metrics_collected", self._reported_metrics)  # pyright: ignore[reportUnknownMemberType]
        self.built.off("error", self._reported_error)  # pyright: ignore[reportUnknownMemberType]
        await self.built.aclose()

    def _reported_metrics(self, *args: object) -> None:
        self.emit("metrics_collected", *args)

    def _reported_error(self, *args: object) -> None:
        self.emit("error", *args)


MODALITIES: tuple[Modality, ...] = ("llm", "stt", "tts")


CLASS_OF: dict[Modality, str] = {"llm": "LLM", "stt": "STT", "tts": "TTS"}


# The org's own knobs and the names a plugin may take each under; one it takes under none is
# refused where the org sets it, not dropped on the next call. `stt_of` gives the endpointing to
# ears that call it `eot_timeout_ms`.
_KNOB_NAMES: dict[str, tuple[str, ...]] = {
    "endpointing_ms": ("endpointing_ms", "eot_timeout_ms"),
    "eot_threshold": ("eot_threshold",),
    "eager_eot_threshold": ("eager_eot_threshold",),
    "voice": _THE_VOICE,
}


# A component that failed is named in its error's label: `label='livekit.plugins.deepgram.stt.STT'`.
_LABELLED = re.compile(
    rf"label='(?:{re.escape(_PLUGINS)}\.(?P<plugin>\w+)|(?P<inference>{re.escape(_INFERENCE_MODULE)}))\."
)


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
    built = _built("llm", _AN_LLM, running, {})
    return LLMWithRequest(built, running.request) if running.request else built


# A model the runtime asks itself, outside a call's session: livekit meters none of it.
def completion_usage(
    model: llm.LLM[Never], used: llm.CompletionUsage | None
) -> LLMModelUsage | None:
    """What one answer of a model cost in tokens, as the call's usage writes it."""
    if used is None:
        return None
    # Frames drop unset fields, so the discriminator is set here or the SDKs reject the entry.
    return LLMModelUsage(
        type="llm_usage",
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


# Only what the org set: the key, the language and the hints are the runtime's, and a plugin
# that takes none of them is the operator's to configure in the providers row.
def refuse_untaken(ears: Running, voice: Running, tuning: Tuning) -> None:
    """Refuse the org's turn and voice knobs that its ears or its voice take under no name."""
    turn = tuning.turn or Turn()
    heard = {
        "endpointing_ms": turn.endpointing_ms,
        "eot_threshold": turn.eot_threshold,
        "eager_eot_threshold": turn.eager_eot_threshold,
    }
    _refuse_untaken("stt", ears, [knob for knob, value in heard.items() if value is not None])
    _refuse_untaken("tts", voice, ["voice"] if tuning.voice is not None else [])


# Every field is one a constructor of the vendor takes, named for a secret and not for where it
# is sent, and a short scalar: nothing a plugin would open, dial or read a file by.
def check_credentials(vendor: str, credentials: Credentials) -> None:
    """Refuse a credentials object that names an address, a file, a session or a field untaken."""
    if not isinstance(credentials, dict):
        return
    if len(credentials) > _MOST_FIELDS:
        raise DeclarationRefused(TOO_MANY_FIELDS.format(vendor=vendor, count=len(credentials)))
    taken = _every_parameter(vendor)
    for name, value in credentials.items():
        lowered = name.lower()
        if lowered in _NOT_A_CREDENTIAL or lowered.endswith(_NOT_A_CREDENTIAL_ENDS):
            raise DeclarationRefused(NOT_A_CREDENTIAL.format(vendor=vendor, field=name))
        if name not in taken:
            fields = ", ".join(sorted(taken)) or "nothing by name"
            raise DeclarationRefused(NOT_TAKEN.format(vendor=vendor, field=name, fields=fields))
        if not isinstance(value, str | int | float | bool) or (
            isinstance(value, str) and (len(value) > _LONGEST_VALUE or "://" in value)
        ):
            raise DeclarationRefused(NOT_A_VALUE.format(vendor=vendor, field=name))


def vendor_named_in(text: str) -> str:
    """The vendor whose plugin a component's error names, "" when it names none."""
    found = _LABELLED.search(text)
    if found is None:
        return ""
    return INFERENCE if found["inference"] else found["plugin"]


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


def _refuse_untaken(modality: Modality, running: Running, knobs: list[str]) -> None:
    if not knobs:
        return
    accepts = _parameters(_class_of(modality, running))
    untaken = [knob for knob in knobs if not any(name in accepts for name in _KNOB_NAMES[knob])]
    if untaken:
        raise DeclarationRefused(
            _UNTAKEN.format(vendor=running.vendor, stage=modality, knobs=", ".join(untaken))
        )


def _class_of(modality: Modality, running: Running) -> type:
    name = running.builds or CLASS_OF[modality]
    made: object = getattr(plugin(running.vendor), name, None)
    if not isinstance(made, type):
        raise DeclarationRefused(
            _NO_SUCH_CLASS.format(vendor=running.vendor, stage=CLASS_OF[modality], name=name)
        )
    return made


def _built[T](
    modality: Modality, base: type[T], running: Running, knobs: Mapping[str, object]
) -> T:
    named = running.builds or CLASS_OF[modality]
    made = _class_of(modality, running)
    if not issubclass(made, base):
        raise DeclarationRefused(
            _NO_SUCH_CLASS.format(vendor=running.vendor, stage=CLASS_OF[modality], name=named)
        )
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
    # A plugin raises what it likes when it refuses (SpitchError, a missing variable): each is
    # the vendor refusing its stage, said in its own words.
    try:
        return constructor(**_shaped(made, given))
    except Exception as refused:
        raise DeclarationRefused(f"{running.vendor} refused its {named}: {refused}") from refused


def _every_parameter(vendor: str) -> frozenset[str]:
    module = plugin(vendor)
    classes = (getattr(module, name, None) for name in CLASS_OF.values())
    taken = frozenset[str]()
    for made in classes:
        if isinstance(made, type):
            taken |= _parameters(made)
    return taken - {"self", "kwargs", "args"}


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
