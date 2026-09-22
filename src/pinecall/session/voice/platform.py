"""What a spoken call asks of the platform: write, run a tool, read back, dial a second leg."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from pinecall._exceptions import PinecallError
from pinecall.types import Env
from pinecall.types.json import JsonObject
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.events import ToolCall


class PlatformRefused(PinecallError):
    """The platform answered anything but yes; the caller of the verb says what that means."""


# A leg is dialled through the org's own trunk and only after the number it dials has passed the
# org's guards (orgs/guards.py), so "no trunk" and "not this number, not this often" are two
# different answers and the verb writes the one that happened into the call's own log.
@dataclass(frozen=True)
class Dialled:
    """What the platform said about dialling a number: the trunk to do it with, or why not."""

    trunk: str | None = None
    refused: str | None = None


# A Protocol and not the worker's HTTP client itself: the bridge is one call's logic and knows
# nothing of transports, and a test scripts a platform in memory with a live tail, which no HTTP
# fake could do as plainly. worker/client.py is the one implementation that leaves the process.
class Platform(Protocol):
    """The doors a spoken call knocks on: one write, one tool, one trunk, the log read back."""

    async def append(
        self, call: str, type: str, data: JsonObject, ephemeral: bool | None = None
    ) -> None:
        """One entry of this call, numbered by the platform: the worker never learns a seq."""
        ...

    async def tool(self, call: str, agent: str, wanted: ToolCall, timeout_s: float) -> ToolResult:
        """One tool through the app's own process and back, or PlatformRefused."""
        ...

    async def outbound_trunk(
        self, slug: str, *, org: str, env: Env, holder: str | None, to: str, call: str
    ) -> Dialled:
        """The trunk this leg dials out through once `to` passed the org's guards, or why not."""
        ...

    async def state(self, call: str) -> tuple[JsonObject, int]:
        """The reduced state of the call and the seq it was folded to, for a widget's snapshot."""
        ...

    def since(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """Every durable entry above the cursor, in seq order, until the log runs out."""
        ...

    def tail(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """The same, then log.caught_up, then live — for as long as the call lasts."""
        ...
