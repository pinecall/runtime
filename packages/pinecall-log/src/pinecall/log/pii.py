"""Masking personal data on the way in: by the names the agent declared, and by what they held."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

from pinecall.types.agent import AgentConfig
from pinecall.types.json import JsonObject

# Everywhere on this platform a masked value is this string. The key stays, so a reader knows a
# value exists; the type is gone, so nothing leaks through its shape. docs/protocol/projections.md.
MASK = "***"

# The words the log is written for. A transcript with holes in it is not a transcript, and a
# metric is a number nobody's name can hide in: neither is ever touched, whatever was declared.
# This is the mistake the references made, and the reason this rule is a constant and not a habit.
UNTOUCHED: tuple[str, ...] = ("turn.", "metrics.", "user.transcript", "agent.transcript")

# The entries the masker learns from instead of masking: the app's state is the tenant's own
# console, and the tenant projection masks it at the sink, where the token says who is reading.
# call.attached carries that same state to the socket that takes a call over, which needs it whole.
LEARNED_FROM = ("state.changed", "call.attached")

# A learned value shorter than this would mask the alphabet: "P" appears in every other word.
SHORTEST_LEARNED_VALUE = 3

# What decoded JSON is, said once so every helper below recurses over a shape and not over Any.
type Json = str | int | float | bool | list["Json"] | dict[str, "Json"] | None


class Masker:
    """Holds one agent's declarations, and everything it has seen under a name declared pii."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._state_fields = _pii_state_fields(config)
        self._tool_arguments = _pii_tool_arguments(config)
        self._learned: list[str] = []

    def mask(self, type: str, data: JsonObject) -> JsonObject:
        """The entry's data as it should be written. state.changed teaches; everything else pays."""
        if type.startswith(UNTOUCHED) or not data:
            return data
        if type in LEARNED_FROM:
            self.learn(_values_under(data.get("state", {}), self._state_fields))
            return data
        named = self._names_in(type, data)
        masked = _mask_by_name(data, named) if named else data
        self.learn(_values_under(data, named))
        return cast("JsonObject", _mask_learned(masked, self._learned))

    def learn(self, values: Iterable[Json]) -> None:
        """Remember what stood under a declared name, longest first, so the long one masks first."""
        for value in _leaf_strings(list(values)):
            if len(value) >= SHORTEST_LEARNED_VALUE and value not in self._learned:
                self._learned.append(value)
        self._learned.sort(key=len, reverse=True)

    @property
    def learned(self) -> tuple[str, ...]:
        """What the masker has seen, longest first. A test reads it; nothing else should."""
        return tuple(self._learned)

    def _names_in(self, type: str, data: JsonObject) -> frozenset[str]:
        """A tool call is masked by the tool's own declaration; every other entry by the state's."""
        if type == "tool.call":
            return self._tool_arguments.get(str(data.get("name", "")), frozenset())
        return self._state_fields


def _pii_state_fields(config: AgentConfig | None) -> frozenset[str]:
    """The state fields the agent declared personal. Undeclared is the tenant's, seen whole."""
    if config is None:
        return frozenset()
    return frozenset(name for name, seen_by in config.state_fields.items() if seen_by == "pii")


def _pii_tool_arguments(config: AgentConfig | None) -> Mapping[str, frozenset[str]]:
    """Per tool, the arguments it declared personal: masked before the entry is ever written."""
    if config is None:
        return {}
    return {tool.name: tool.pii for tool in config.tools if tool.pii}


def _mask_by_name(value: Json, names: frozenset[str]) -> Json:
    """Every key by one of these names, however deep, loses its value and keeps its key."""
    if isinstance(value, dict):
        return {
            key: MASK if key in names else _mask_by_name(item, names) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_by_name(item, names) for item in value]
    return value


def _mask_learned(value: Json, learned: list[str]) -> Json:
    """Every string that carries something learned, masked in place, the longest match first."""
    if isinstance(value, str):
        for known in learned:
            value = value.replace(known, MASK)
        return value
    if isinstance(value, dict):
        return {key: _mask_learned(item, learned) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_learned(item, learned) for item in value]
    return value


def _values_under(value: Json, names: frozenset[str]) -> list[Json]:
    """Whatever stood under one of these names, however deep. This is what the masker learns."""
    found: list[Json] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                found.append(item)
            else:
                found.extend(_values_under(item, names))
    elif isinstance(value, list):
        for item in value:
            found.extend(_values_under(item, names))
    return found


def _leaf_strings(values: list[Json]) -> list[str]:
    """A declared field can hold an object; what a masker can match is the strings inside it."""
    found: list[str] = []
    for value in values:
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            found.extend(_leaf_strings(list(value.values())))
        elif isinstance(value, list):
            found.extend(_leaf_strings(value))
    return found
