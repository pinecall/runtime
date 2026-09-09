"""The prompt's regions on a live agent: the prefix rewritten only when it changed, and the view."""

from __future__ import annotations

from collections.abc import Sequence

from livekit.agents import llm as agents
from livekit.agents.voice import Agent

from pinecall_protocol import defs


# Two regions, because they age differently: the static one is livekit's `instructions`, the one
# pinned item at index 0 that Anthropic's cache breakpoint lands on, and the view is rendered from
# state every turn and appended to the request after the history. update_tools writes an
# AgentConfigUpdate into the history (agent_activity.py:631) which no formatter sends, so the
# tools may change all day and the prefix the provider reads stays byte for byte the same.
class Regions:
    """One call's prompt as livekit holds it: the instructions, its own history, and our view."""

    def __init__(self, agent: Agent, static: str = "") -> None:
        self._agent = agent
        self._static = static
        self._view = ""

    @property
    def static(self) -> str:
        """The cached prefix, as the model last read it."""
        return self._static

    @property
    def view(self) -> str:
        """The dynamic region, read per request so it never enters the cached prefix."""
        return self._view

    async def set_prompt(self, region: defs.PromptRegion, text: str) -> None:
        """prompt.set: the static region is livekit's instructions, the view is read per request."""
        if region == "view":
            self._view = text
            return
        # Rewriting the prefix with the same bytes still invalidates the cache, so the same text
        # twice is no update at all.
        if text == self._static:
            return
        self._static = text
        await self._agent.update_instructions(text)

    async def set_tools(self, tools: Sequence[agents.Tool | agents.Toolset]) -> None:
        """tools.set: what the model may call from here on. The instructions are not touched."""
        await self._agent.update_tools(list(tools))
