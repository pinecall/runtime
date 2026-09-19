"""What one agent hears, decides and speaks with right now, and what those three cost it."""

from __future__ import annotations

import dataclasses

from pinecall._settings import Settings
from pinecall.api.providers import ProviderRow, rows
from pinecall.log.entry import Entry
from pinecall.log.latencies import medians
from pinecall.log.store import Store
from pinecall.providers.catalog import settings_field_of
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.pipeline import DEFAULT_STT, DEFAULT_TTS, vendor_running
from pinecall.providers.registry import NO_KEY
from pinecall.providers.tts.voices import voice_names
from pinecall.providers.tuning import tuned
from pinecall.types import AgentConfig, Greeting, Lexicon, Model, Tuning, Voice
from pinecall_protocol import WireModel, defs

# How many of the agent's calls the medians are taken over. Enough that one bad morning does not
# read as the pipeline's normal, few enough that the door answers while a person is looking at it.
LAST_CALLS = 20


# The six knobs the old door takes, kept one release for the console's Pipeline screen: a knob
# left out is not set. What they set lives in the agent's tuning now (api/tuning.py), so a body
# here becomes the next version of the corner's tuning with these six replaced and the rest kept.
class Overridden(WireModel):
    """The six knobs an operator turns at PUT …/pipeline/overrides. A knob left out is not set."""

    voice: str | None = None
    tts: str | None = None
    tts_model: str | None = None
    stt: str | None = None
    llm: str | None = None
    greeting: str | None = None


def turned(standing: Tuning, knobs: Overridden) -> Tuning:
    """The corner's tuning with the six knobs replaced whole, as the old door replaced them."""
    return dataclasses.replace(
        standing,
        voice=knobs.voice,
        tts=knobs.tts,
        tts_model=knobs.tts_model,
        stt=knobs.stt,
        llm=knobs.llm,
        greeting=None if knobs.greeting is None else Greeting(say=knobs.greeting),
    )


def overridden_of(tuning: Tuning) -> Overridden:
    """The six knobs as the report still draws them, off what the corner's tuning says."""
    greeting = tuning.greeting
    return Overridden(
        voice=tuning.voice,
        tts=tuning.tts,
        tts_model=tuning.tts_model,
        stt=tuning.stt,
        llm=tuning.llm,
        greeting=None if greeting is None else greeting.say,
    )


class Stage(WireModel):
    """One of the three: which vendor runs it, which model, and the one knob worth showing."""

    vendor: str
    model: str | None = None
    voice_id: str | None = None
    language: str | None = None


class Measured(WireModel):
    """One latency over the agent's recent calls: livekit's name, the median, how many turns."""

    name: str
    seconds: float
    turns: int


class Report(WireModel):
    """The whole screen in one answer: the stages, their cost, what is turned, what may be asked."""

    agent: str
    hears: Stage
    decides: Stage
    speaks: Stage
    # The class's own opening, in the wire's shape: the screen must be able to say whether
    # this agent reads words out or tells the model to find its own, because the knob below
    # only ever sets words and would otherwise look like it changed nothing.
    greeting: defs.GreetingConfig | None
    overrides: Overridden
    # The names the voice knob may be turned to, read off the one table: a screen that offered a
    # free text box let an operator paste an id no vendor knows, which ends a line and not a form.
    voices: list[str]
    # Every vendor each stage could be turned onto, with whether this box can run it — the same
    # rows GET /v1/providers answers, built by the same function (api/providers.py). The screen
    # that changes a stage is where a person needs to see that Cartesia exists and wants a key.
    providers: list[ProviderRow]
    calls: int
    medians: list[Measured]
    unavailable_reasons: dict[str, str]


# The stages are read off the config the NEXT session would be built on — what the app declared
# with the corner's tuning laid over it — so the screen never shows a vendor that is no longer
# the one in use.
async def report(
    agent: str,
    declared: AgentConfig,
    tuning: Tuning,
    lexicon: Lexicon,
    store: Store,
    settings: Settings,
) -> Report:
    """Read the agent's last calls once and answer everything the pipeline screen draws."""
    config = tuned(declared, tuning, lexicon)
    hears = _hears(config.stt, config.language)
    decides = _decides(config.llm)
    speaks = _speaks(config.voice, config.language)
    calls = await _the_last_calls(agent, store)
    return Report(
        agent=agent,
        hears=hears,
        decides=decides,
        speaks=speaks,
        greeting=_on_the_wire(config.greeting),
        overrides=overridden_of(tuning),
        voices=list(voice_names()),
        providers=rows(settings),
        calls=len(calls),
        medians=[
            Measured(name=row.name, seconds=row.seconds, turns=row.turns)
            for row in medians(_entries_of(calls))
        ],
        unavailable_reasons=_unavailable(
            {"hears": hears, "decides": decides, "speaks": speaks}, settings
        ),
    )


def _on_the_wire(greeting: Greeting | None) -> defs.GreetingConfig | None:
    """The opening as the console reads it: the same two fields the class declared."""
    if greeting is None:
        return None
    return defs.GreetingConfig(
        say=greeting.say, reply=greeting.reply, allow_interruptions=greeting.allow_interruptions
    )


# ── the three stages, as the pipeline would build them ──────────────────────────


def _hears(declared: Model | None, language: str | None) -> Stage:
    """What turns the caller's voice into words: providers/pipeline.py `_hearing`, on screen."""
    return Stage(
        vendor=vendor_running(declared, DEFAULT_STT),
        model=declared.model if declared else None,
        language=language,
    )


def _decides(declared: Model | None) -> Stage:
    """What answers: providers/pipeline.py `_thinking`, on screen."""
    return Stage(
        vendor=vendor_running(declared, DEFAULT_VENDOR),
        model=declared.model if declared else None,
    )


def _speaks(declared: Voice | None, language: str | None) -> Stage:
    """What says it out loud: providers/pipeline.py `_speaking`, on screen."""
    return Stage(
        vendor=vendor_running(declared, DEFAULT_TTS),
        model=declared.model if declared else None,
        voice_id=declared.voice_id if declared else None,
        language=language,
    )


# ── what the calls measured ─────────────────────────────────────────────────────


async def _the_last_calls(agent: str, store: Store) -> list[list[Entry]]:
    """The entries of the agent's most recent calls, newest last, one list per call."""
    ids = await store.list_calls(agent)
    return [await store.since(call) for call in ids[-LAST_CALLS:]]


# The medians are taken over every turn of every call together, not over one median per call: a
# call of two turns must not weigh as much as a call of forty. log/latencies.py holds the rule.
def _entries_of(calls: list[list[Entry]]) -> list[Entry]:
    """Every entry of the read calls, in one list for the one median rule to reduce."""
    return [entry for call in calls for entry in call]


# The sentence is providers/registry.py's, because that is where a call reads a key and refuses
# without one, and which field holds it is providers/catalog.py's one rule. Said here BEFORE the
# call, so a screen shows a missing key as a state and not as a dead line — and an org's own key,
# which this screen never sees, is not read here: what it answers is what the BOX has, which is the
# question an operator is asking.
def _unavailable(stages: dict[str, Stage], settings: Settings) -> dict[str, str]:
    """The stages that cannot run today, each with the reason: a vendor key nobody set."""
    missing: dict[str, str] = {}
    for where, stage in stages.items():
        setting = settings_field_of(stage.vendor)
        if setting is not None and not getattr(settings, setting, None):
            missing[where] = NO_KEY.format(vendor=stage.vendor)
    return missing
