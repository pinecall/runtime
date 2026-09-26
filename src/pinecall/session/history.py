"""The one moment the platform reaches into livekit's history: items onto its end, no request."""

from __future__ import annotations

from livekit.agents import llm as agents
from livekit.agents.voice import Agent


# livekit owns the history: the copy-extend-update is its own way of appending
# (voice/agent.py:236, update_chat_ctx), and this is the one place the tree spells it. Nothing
# goes into a static block: those are what the provider caches, and a sentence appended there
# would rebuild the cache for every call this agent ever answers.
async def remembered(agent: Agent, *items: agents.ChatItem) -> None:
    """Items onto the end of the history without a turn of the model: agent.say is not one, and
    neither is the clock's pair. Invalid function calls stay: a tool the agent does not hold is
    context here, not something the model may call again."""
    context = agent.chat_ctx.copy()
    context.items.extend(items)
    await agent.update_chat_ctx(context, exclude_invalid_function_calls=False)


async def noted(agent: Agent, note: str) -> None:
    """One system message onto the end of the history, where the model reads it next turn."""
    context = agent.chat_ctx.copy()
    context.add_message(role="system", content=note)
    await agent.update_chat_ctx(context)
