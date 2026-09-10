"""What the model was actually asked, turn by turn: the one thing the log leaves out on purpose."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from livekit.agents import llm as agents
from livekit.agents.llm.utils import (
    build_legacy_openai_schema,  # pyright: ignore[reportUnknownVariableType]
)

from pinecall.providers.blocks import SystemBlocks, as_a_request


class Asking(Protocol):
    """The session's hand on its own requests, at the moment one is handed to the provider."""

    def asked(self, request: SystemBlocks, tools: Sequence[agents.Tool], vendor: str) -> None:
        """One request, built and not yet sent. Whoever is listening decides what to do with it."""
        ...


class NotAsking:
    """What every real call runs: the prompt is kept nowhere, which is why the log holds a hash."""

    def asked(
        self,
        request: SystemBlocks,  # noqa: ARG002 — the protocol's signature
        tools: Sequence[agents.Tool],  # noqa: ARG002 — the protocol's signature
        vendor: str,  # noqa: ARG002 — the protocol's signature
    ) -> None:
        """Nothing at all, and no formatter is run: a live call must not pay for a copy."""
        return


class WhatWasAsked:
    """Every request of one call, in order, as the vendor's own formatter built it."""

    def __init__(self) -> None:
        self._turns: list[Mapping[str, Any]] = []

    def asked(self, request: SystemBlocks, tools: Sequence[agents.Tool], vendor: str) -> None:
        """Keep this one: once per request, which on a turn that runs tools is more than once."""
        self._turns.append({**as_a_request(request, vendor), "tools": _declared_as(tools)})

    @property
    def turns(self) -> tuple[Mapping[str, Any], ...]:
        """What the model read, request by request: on a broken golden, the thing to open first."""
        return tuple(self._turns)


# The tools ARE part of the prompt: a provider caches them ahead of the system blocks, and a model
# that ran none of them is judged on the list it was actually handed — which is every declared
# tool, always, because narrowing it per stage would throw the cache away. session/visibility.py.
def _declared_as(tools: Sequence[agents.Tool]) -> list[dict[str, Any]]:
    """Every tool the request carries, as a JSON schema, in the order the provider receives it."""
    # livekit's FunctionTool is generic over an unbounded parameter, so a strict checker reads both
    # its own guard and its own schema builder as partially unknown. One cast each, at the one call.
    written = cast("Callable[[Any], bool]", agents.is_function_tool)  # pyright: ignore[reportUnknownMemberType]
    schema = cast("Callable[[Any], dict[str, Any]]", build_legacy_openai_schema)
    return [schema(tool) for tool in tools if written(tool)]
