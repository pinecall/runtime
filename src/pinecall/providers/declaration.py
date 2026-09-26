"""The wire's declaration as the domain's: one conversion, by name, at the edge of the gateway."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from pinecall.types import DEFAULT_LAYOUT, AgentConfig, Env, PromptBlock, Route, ToolSpec
from pinecall.types.agent import EventSource, Visibility
from pinecall.types.channel import CHANNELS_WITH_A_NUMBER
from pinecall_protocol import defs
from pinecall_protocol.events import AgentRegistered

# The wire leaves timeout_s absent when the app did not say; the domain's default is the number
# the platform promises, and it is written down once, in the contract.
DEFAULT_TIMEOUT_S: float = ToolSpec.timeout_s


def parse_tool(wire: defs.ToolSpec) -> ToolSpec:
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


def agent_from_slug(slug: str) -> AgentConfig:
    """What a register alone declares: a slug. Every other field is the class's or the world's."""
    return AgentConfig(slug=slug)


# Only the fields the app sent change: a configure is a patch, and pydantic remembers which keys
# were on the wire. An absent field keeps whatever the agent declared before. The environment the
# wire still carries — a voice, the models, an opening, what is remembered, a base — is the
# world's now (Tuning) and is not read: an app on an older package registers all the same.
def apply_declaration(current: AgentConfig, wire: defs.AgentConfig) -> AgentConfig:
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
    if "uses_knowledge" in sent:
        converted["uses_knowledge"] = wire.uses_knowledge
    if "tools" in sent:
        converted["tools"] = tuple(parse_tool(tool) for tool in wire.tools or ())
    if "state_fields" in sent:
        converted["state_fields"] = _visibilities(wire.state_fields or ())
    if "view" in sent:
        # The wire carries a shape so it can grow; the domain carries the one thing in it, which
        # is the name a console titles the panel with.
        converted["view"] = wire.view.name if wire.view is not None else None
    if "events" in sent:
        converted["events"] = _senders(wire.events or ())
    return converted


def _a_layout(specs: Sequence[defs.PromptBlockSpec] | None) -> tuple[PromptBlock, ...]:
    """The wire's list of {name, region} as the prompt's layout; nothing declared is the default."""
    if not specs:
        return DEFAULT_LAYOUT
    return tuple(PromptBlock(spec.name, spec.region) for spec in specs)


def _visibilities(specs: Sequence[defs.StateFieldSpec]) -> dict[str, Visibility]:
    """The wire's list of {name, visibility} as the mapping a field's rule is read from."""
    return {spec.name: spec.visibility for spec in specs}


def _senders(specs: Sequence[defs.EventSpec]) -> dict[str, frozenset[EventSource]]:
    """The wire's list of {name, from} as the mapping an outside fact is checked against."""
    return {spec.name: frozenset(spec.from_) for spec in specs}


# What the gateway says back when it accepts a claim, built here because this module is where a
# route crosses between the wire's shape and the domain's, in both directions. encode() drops what
# nobody set, so an optional field is left out rather than sent as null: the schema says `label` is
# a string when it is there, and null is not a string.
def build_registered(app: str, sdk: str | None, env: Env) -> AgentRegistered:
    """The agent.registered payload: the socket's id, the world, the SDK. A door is a row now."""
    said: dict[str, Any] = {"app": app, "routes": [], "env": env}
    if sdk is not None:
        said["sdk"] = sdk
    return AgentRegistered(**said)


def wire_route(route: Route) -> defs.Route:
    """The domain's route as the wire says it back: the door, without the org that owns it."""
    door: dict[str, Any] = {"channel": route.channel, "number": route.number}
    if route.label is not None:
        door["label"] = route.label
    return defs.Route(**door)


def rang(route: Route) -> bool:
    """Whether a call at this route ARRIVED at a door, rather than being opened by a key holder.

    The two are served by different corners of a world: what a key opened lands in the holder's,
    and what rang lands on the agent's line, because a number is the org's door and the worker
    that dialled it holds a key naming nobody. See api/agents/dial_in.py.
    """
    return route.channel in CHANNELS_WITH_A_NUMBER
