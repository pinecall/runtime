"""Any catalogued vendor, out of its own livekit plugin, with no file of ours per vendor."""

# A tuned file under `llm/`, `stt/` or `tts/` exists when this runtime has something to say about a
# vendor — a default the plugin gets wrong, an endpointing number, a model this build forbids. For
# the other forty, there is nothing to say: the plugin IS the adapter, and a file per vendor would
# be forty copies of `Class(model=…, api_key=…)` that each go stale on their own schedule.
#
# So the arguments are not written down. Every plugin disagrees about how to spell a voice —
# `voice`, `voice_id`, `voice_uuid`, `speaker`, `voice_name` — and a table of that is wrong the day
# livekit releases. What is read instead is the plugin's own `__init__` signature, at the moment one
# is built: the first name it actually declares is the one it is sent, and a thing it declares no
# name for is simply not sent. A plugin that changes its mind changes this file's behaviour with it.

from __future__ import annotations

import inspect
from collections.abc import Iterable
from importlib import import_module, util
from typing import Any

from pinecall._exceptions import PinecallError
from pinecall.providers.catalog import Modality, Provider

# The names one thing goes by, in the order this door tries them. `voice_id` leads `voice` because
# two plugins declare both and the id is the one a vendor answers for.
THE_KEY: tuple[str, ...] = ("api_key", "speech_key", "secret")
THE_VOICE: tuple[str, ...] = ("voice_id", "voice_uuid", "voice", "speaker", "voice_name")
# Only the two that take one string. `languages` and `language_codes` want a list, and a bare
# string handed to either is a vendor error in the middle of a caller's turn.
THE_LANGUAGE: tuple[str, ...] = ("language", "language_code")

# The class each modality's plugin exports, which is the same three letters in every one of them.
CLASS_OF: dict[str, str] = {"llm": "LLM", "stt": "STT", "tts": "TTS"}

# Named with the command that fixes it, and with the way round it that needs no command at all:
# LiveKit Inference fronts most of these vendors on the box's own LiveKit project.
NOT_INSTALLED = (
    '{vendor} has no plugin in this build — install it with `pip install "livekit-agents[{extra}]"`'
    ", or declare the vendor `livekit` and name the model `{vendor}/<model>`, which needs neither"
    " a plugin nor a key of that vendor's own"
)

# A catalogued vendor whose plugin exports no class for this job. The catalog says which jobs a
# vendor does, so this is a row that disagrees with its plugin — a bug here, not a typo out there.
NO_CLASS = "{vendor}'s plugin has no {modality}: providers/catalog.py says it does and it does not"

# Several vendors want more than a key: Baseten an endpoint, RTZR a client id and a secret, slng a
# model, Speechmatics a VAD plugin. They say so themselves, in their own words, and their words are
# better than anything this file could invent — so the sentence is theirs and the shape is ours.
VENDOR_REFUSED = "{vendor} would not build its {modality}: {said}"

# And two plugins livekit ships do not import at all in this build — upliftai's wheel forgets
# python-socketio, clova's pulls pydub, which imports the `audioop` module 3.12 deprecated. That is
# a packaging fact about the plugin, not a typo out here, and the importer's own sentence says it.
WILL_NOT_IMPORT = "{vendor}'s plugin is installed and does not import: {said}"


class PluginProblem(PinecallError):
    """This build cannot make that vendor's object: a refusal either way, never a crash."""


class PluginMissing(PluginProblem):
    """The vendor is real and this build has no plugin installed for it."""


class VendorRefused(PluginProblem):
    """The plugin is there and it wants something a key is not: an endpoint, a pair, a model."""


def installed(provider: Provider) -> bool:
    """Whether this build can reach the vendor at all. False is a missing extra, never a typo."""
    if not provider.plugin:
        return True  # `livekit` is livekit-agents itself: there is nothing to install
    return util.find_spec(f"livekit.plugins.{provider.plugin}") is not None


# `key` is None for the two kinds of vendor that have no single one: the ones whose credentials are
# a chain of their own (AWS, Google's speech pair) and the ones this build has no key for at all.
# The plugin then reads its own environment, which is exactly what such a vendor expects.
def built_by_a_plugin(
    modality: Modality,
    provider: Provider,
    *,
    key: str | None = None,
    model: str | None = None,
    voice_id: str | None = None,
    language: str | None = None,
) -> Any:
    """One vendor object out of its plugin, sent only what that plugin declares a name for."""
    made = _the_class(modality, provider)
    takes = _what_it_takes(made)
    wanted: dict[str, Any] = {}
    _put(wanted, takes, THE_KEY, key)
    _put(wanted, takes, ("model",), model)
    if modality == "tts":
        _put(wanted, takes, THE_VOICE, voice_id)
    if modality != "llm":
        _put(wanted, takes, THE_LANGUAGE, language)
    return _made(made, wanted, modality, provider)


# The construction itself, and only it, is inside the try: a plugin that raises here has refused
# the call before it started, which is the whole promise of building the pipeline up front. Letting
# a ValueError out instead would end the JOB — with a caller already in the room — as a traceback
# nobody reads, rather than as a line on the screen that says what the vendor wants.
def _made(made: Any, wanted: dict[str, Any], modality: Modality, provider: Provider) -> Any:
    """The vendor object, or its own refusal in its own words, as a refusal of ours."""
    try:
        return made(**wanted)
    except PinecallError:
        raise
    except Exception as refused:
        said = VENDOR_REFUSED.format(vendor=provider.name, modality=modality, said=refused)
        raise VendorRefused(said) from refused


def _put(wanted: dict[str, Any], takes: frozenset[str], names: Iterable[str], value: Any) -> None:
    """Send one thing under the first name the plugin declares for it, or not at all."""
    if value is None:
        return
    for name in names:
        if name in takes:
            wanted[name] = value
            return


def _the_class(modality: Modality, provider: Provider) -> Any:
    """The plugin's LLM, STT or TTS, imported now. PluginMissing names what installs it."""
    if not installed(provider):
        raise PluginMissing(NOT_INSTALLED.format(vendor=provider.name, extra=provider.extra))
    try:
        module = import_module(f"livekit.plugins.{provider.plugin}")
    except Exception as broken:
        raise VendorRefused(WILL_NOT_IMPORT.format(vendor=provider.name, said=broken)) from broken
    made = getattr(module, CLASS_OF[modality], None)
    if made is None:
        raise PluginMissing(NO_CLASS.format(vendor=provider.name, modality=modality))
    return made


# The signature, and never a guess: a plugin that takes **kwargs and declares nothing would be sent
# nothing, which is the safe way to be wrong — it then runs on its own environment and its own
# defaults, rather than raising TypeError with a caller already in the room.
def _what_it_takes(made: Any) -> frozenset[str]:
    """Every keyword the plugin's constructor declares by name."""
    try:
        parameters = inspect.signature(made.__init__).parameters
    except (TypeError, ValueError):  # a C constructor, or one signature cannot read
        return frozenset()
    return frozenset(parameters) - {"self"}
