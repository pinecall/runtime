"""The shapes the worker's hop carries: the classes both processes hold, adapted by pydantic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, TypeAdapter

from pinecall.fleet import Heartbeat, Standing
from pinecall.types import AgentConfig, CallContext, Route
from pinecall_protocol import Command
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.rest import Judging


class HoldAudioSaid(BaseModel):
    """What the gateway says an agent plays while a tool runs (api/agents/hold_melody.py)."""

    played: Literal["default", "off", "custom"]
    sha256: str | None = None


# The hop carries the domain object itself, adapted by pydantic. The one wire-to-domain conversion
# in the tree is providers/declaration.py, at the app's edge, and this is deliberately not a
# second one: two processes of the same distribution exchange the class they both already hold.
ROUTES: TypeAdapter[tuple[Route, ...]] = TypeAdapter(tuple[Route, ...])
CONFIG: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)
KEYS: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])
LENDS: TypeAdapter[list[str]] = TypeAdapter(list[str])
CONTEXT: TypeAdapter[CallContext] = TypeAdapter(CallContext)
RESULT: TypeAdapter[ToolResult] = TypeAdapter(ToolResult)
COMMAND: TypeAdapter[Command] = TypeAdapter(Command)
BEAT: TypeAdapter[Heartbeat] = TypeAdapter(Heartbeat)
STANDING: TypeAdapter[Standing] = TypeAdapter(Standing)
JUDGING: TypeAdapter[Judging] = TypeAdapter(Judging)
