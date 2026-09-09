"""Which declared tools the model may call right now: tools.set narrows it, the callable asks."""

from __future__ import annotations

from collections.abc import Sequence

from livekit.agents.llm import ToolError

from pinecall.log import REFUSED
from pinecall.session.pending import Emit
from pinecall.types import AgentConfig
from pinecall_protocol import defs
from pinecall_protocol.events import ErrorEvent

# What the model reads back when it calls a tool the app has closed in this state.
NOT_AVAILABLE = "{name} is not available now"


# The livekit Agent is built once with EVERY declared tool and never re-declared. A tool
# definition is the first thing in a provider's cached prefix — Anthropic: "Modifying tool
# definitions (names, descriptions, parameters) invalidates the entire cache", and OpenAI's prefix
# cache reads the same order — so an update_tools on every stage change threw the whole cache
# away, system blocks included. The subset the app opens lives here instead, one per call, and is
# enforced in our own callable before anything reaches the app. docs/decisions/prompt-blocks.md.
class Visibility:
    """The declared tools the app has opened, and the gate that holds the others shut."""

    def __init__(self, config: AgentConfig) -> None:
        self._declared = config.tools_by_name
        # Everything declared is open until a tools.set narrows it.
        self._open: tuple[str, ...] = tuple(self._declared)

    def narrow(self, tools: Sequence[defs.ToolSpec]) -> tuple[str, ...]:
        """tools.set: from here on the model may call these, among what was declared, in order."""
        self._open = tuple(tool.name for tool in tools if tool.name in self._declared)
        return self._open

    # A refused call is written as an error of the platform's and never as a tool.call: that entry
    # is what the app's process runs a tool on, and a tool the app closed must not run there.
    async def admitted(self, name: str, emit: Emit) -> None:
        """The gate: a closed tool is an error the model reads, and the log says who refused it."""
        if name in self._open:
            return
        why = NOT_AVAILABLE.format(name=name)
        await emit("error", ErrorEvent(code=REFUSED, message=why, recoverable=True))
        raise ToolError(why)
