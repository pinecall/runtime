"""The wire's declaration as the domain's: one conversion, by name, at the edge of the gateway."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from pinecall.providers.tts import voices
from pinecall.types import (
    DEFAULT_LAYOUT,
    AgentConfig,
    Docs,
    Env,
    Greeting,
    Hangup,
    KnowledgeFile,
    MemoryPolicy,
    Model,
    PromptBlock,
    Route,
    ToolSpec,
    Turn,
    Voice,
)
from pinecall.types.agent import EventSource, Visibility
from pinecall.types.channel import CHANNELS_WITH_A_NUMBER, Channel
from pinecall_protocol import defs
from pinecall_protocol.events import AgentRegistered

# The wire leaves timeout_s absent when the app did not say; the domain's default is the number
# the platform promises, and it is written down once, in the contract.
DEFAULT_TIMEOUT_S: float = ToolSpec.timeout_s


def a_route(org: str, env: Env, agent: str, wire: defs.Route) -> Route:
    """One door, with the org and the world the key named and the agent this socket speaks for."""
    return Route(
        org=org,
        agent=agent,
        channel=wire.channel,
        number=wire.number,
        label=wire.label,
        env=env,
    )


def a_tool(wire: defs.ToolSpec) -> ToolSpec:
    """One tool. The wire carries both of the domain's shapes now, so nothing is projected."""
    return ToolSpec(
        name=wire.name,
        description=wire.description,
        parameters=wire.parameters,
        side_effect=wire.side_effect,
        pii=frozenset(wire.pii or ()),
        confirm=wire.confirm,
        timeout_s=DEFAULT_TIMEOUT_S if wire.timeout_s is None else wire.timeout_s,
    )


def an_agent(slug: str, routes: Sequence[Route]) -> AgentConfig:
    """What a register alone declares: a slug, and the channels its doors open on."""
    return AgentConfig(slug=slug, channels=frozenset(_channels(routes)))


# Only the fields the app sent change: a configure is a patch, and pydantic remembers which keys
# were on the wire. An absent field keeps whatever the agent declared before.
def configured(current: AgentConfig, wire: defs.AgentConfig) -> AgentConfig:
    """The config with the sent fields replaced. DeclarationRefused when one breaks its rule."""
    return dataclasses.replace(current, **_sent(wire))


def changed_by(wire: defs.AgentConfig) -> tuple[str, ...]:
    """The names of the fields this configure carries, for the agent.configured it lands as."""
    return tuple(sorted(wire.model_fields_set))


# One line per field, in the schema's own order, so the reader can diff this against defs.json.
def _sent(wire: defs.AgentConfig) -> dict[str, Any]:
    """Every field the app put on the wire, converted, under the domain's own field name."""
    sent = wire.model_fields_set
    converted: dict[str, Any] = {}
    if "prompt" in sent:
        converted["prompt"] = _a_layout(wire.prompt)
    if "language" in sent:
        converted["language"] = wire.language
    if "greeting" in sent:
        converted["greeting"] = _a_greeting(wire.greeting)
    if "voice" in sent:
        converted["voice"] = _a_voice(wire.voice)
    if "llm" in sent:
        converted["llm"] = _a_model(wire.llm)
    if "stt" in sent:
        converted["stt"] = _a_model(wire.stt)
    if "turn" in sent:
        converted["turn"] = _a_turn(wire.turn)
    if "says" in sent:
        converted["says"] = _pronunciations(wire.says or ())
    if "hears" in sent:
        converted["hears"] = tuple(wire.hears or ())
    if "knowledge" in sent:
        converted["knowledge"] = _a_knowledge_file(wire.knowledge)
    if "docs" in sent:
        converted["docs"] = _the_docs(wire.docs)
    if "memory" in sent:
        converted["memory"] = _a_memory_policy(wire.memory)
    if "hangup" in sent:
        converted["hangup"] = _a_hangup(wire.hangup)
    if "tools" in sent:
        converted["tools"] = tuple(a_tool(tool) for tool in wire.tools or ())
    if "state_fields" in sent:
        converted["state_fields"] = _visibilities(wire.state_fields or ())
    if "events" in sent:
        converted["events"] = _senders(wire.events or ())
    return converted


def _a_layout(specs: Sequence[defs.PromptBlockSpec] | None) -> tuple[PromptBlock, ...]:
    """The wire's list of {name, region} as the prompt's layout; nothing declared is the default."""
    if not specs:
        return DEFAULT_LAYOUT
    return tuple(PromptBlock(spec.name, spec.region) for spec in specs)


# The vendor is only ever sent an id. `voice = "carolina"` once reached ElevenLabs as a voice_id
# and came back 1008 seven times in one call, so the name is resolved here, while the app is
# declaring itself: an unknown one is a declaration refused, not a caller listening to silence.
def _a_voice(wire: defs.VoiceConfig | None) -> Voice | None:
    if wire is None:
        return None
    speaking = voices.voice_declared(wire.name, wire.provider, wire.voice_id)
    return dataclasses.replace(speaking, model=wire.model)


def _a_model(wire: defs.ModelConfig | None) -> Model | None:
    return None if wire is None else Model(wire.provider, wire.model, wire.temperature)


def _a_turn(wire: defs.TurnConfig | None) -> Turn | None:
    return None if wire is None else Turn(wire.min_interruption_words, wire.endpointing_ms)


def _a_knowledge_file(wire: defs.KnowledgeFile | None) -> KnowledgeFile | None:
    return None if wire is None else KnowledgeFile(wire.path, wire.text)


def _the_docs(wire: defs.DocsConfig | None) -> Docs | None:
    if wire is None:
        return None
    return Docs(base=wire.base, mode=wire.mode, k=wire.k, min_score=wire.min_score)


def _a_memory_policy(wire: defs.MemoryConfig | None) -> MemoryPolicy | None:
    if wire is None:
        return None
    return MemoryPolicy(remember=tuple(wire.remember), forget=tuple(wire.forget))


# DeclarationRefused out of Greeting itself when neither verb or both were sent: the rule is one
# rule, held by the shape, and this door only hands it the wire's own fields.
def _a_greeting(wire: defs.GreetingConfig | None) -> Greeting | None:
    if wire is None:
        return None
    return Greeting(say=wire.say, reply=wire.reply, allow_interruptions=wire.allow_interruptions)


def _a_hangup(wire: defs.HangupConfig | None) -> Hangup | None:
    if wire is None:
        return None
    return Hangup(when=wire.when)


def _pronunciations(said: Sequence[defs.Pronunciation]) -> dict[str, str]:
    """The wire's list of {word, spoken} as the map the voice's replace transform is built from."""
    return {one.word: one.spoken for one in said}


def _visibilities(specs: Sequence[defs.StateFieldSpec]) -> dict[str, Visibility]:
    """The wire's list of {name, visibility} as the mapping a field's rule is read from."""
    return {spec.name: spec.visibility for spec in specs}


def _senders(specs: Sequence[defs.EventSpec]) -> dict[str, frozenset[EventSource]]:
    """The wire's list of {name, from} as the mapping an outside fact is checked against."""
    return {spec.name: frozenset(spec.from_) for spec in specs}


def _channels(routes: Sequence[Route]) -> set[Channel]:
    """An agent's channels are the channels of its doors; it never declares them twice."""
    return {route.channel for route in routes}


# What the gateway says back when it accepts a claim, built here because this module is where a
# route crosses between the wire's shape and the domain's, in both directions. encode() drops what
# nobody set, so an optional field is left out rather than sent as null: the schema says `label` is
# a string when it is there, and null is not a string.
def registered(app: str, routes: Sequence[Route], sdk: str | None, env: Env) -> AgentRegistered:
    """The agent.registered payload: the socket's id, the doors as the wire says them, the SDK."""
    said: dict[str, Any] = {"app": app, "routes": [a_door(route) for route in routes], "env": env}
    if sdk is not None:
        said["sdk"] = sdk
    return AgentRegistered(**said)


def a_door(route: Route) -> defs.Route:
    """The domain's route as the wire says it back: the door, without the org that owns it."""
    door: dict[str, Any] = {"channel": route.channel, "number": route.number}
    if route.label is not None:
        door["label"] = route.label
    return defs.Route(**door)


def dialled(routes: Sequence[Route]) -> tuple[Route, ...]:
    """The routes somebody dials. A web route names no door: what identifies it is its agent."""
    return tuple(route for route in routes if route.channel in CHANNELS_WITH_A_NUMBER)
