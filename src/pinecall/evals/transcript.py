"""What a judge reads off a ChatContext: the agent's words, the tools it called, their answers."""

from __future__ import annotations

from livekit.agents.llm import ChatContext, ChatMessage, FunctionCall, FunctionCallOutput

from pinecall.evals.case import AGENT, CALLER

# livekit discriminates its own items by `type` and reads them that way itself
# (evals/judge.py:59-87), so every reader here does the same: one word, and no isinstance chain
# that would have to grow a branch the day a sixth kind of item arrives.


def said_by_the_agent(chat_ctx: ChatContext) -> tuple[str, ...]:
    """Every turn the agent spoke, in order. Most hard policies are a question about these."""
    return tuple(
        item.text_content or ""
        for item in chat_ctx.items
        if isinstance(item, ChatMessage) and item.role == AGENT
    )


def said_by_the_caller(chat_ctx: ChatContext) -> tuple[str, ...]:
    """Every turn the caller got through: what the agent HEARD, not what was said at it."""
    return tuple(
        item.text_content or ""
        for item in chat_ctx.items
        if isinstance(item, ChatMessage) and item.role == CALLER
    )


def tools_called(chat_ctx: ChatContext) -> tuple[str, ...]:
    """The name of every tool this conversation called, in the order it called them."""
    return tuple(item.name for item in chat_ctx.items if isinstance(item, FunctionCall))


def answered_by_the_app(chat_ctx: ChatContext) -> tuple[str, ...]:
    """What every tool answered with, in call order: the evidence a stated fact may rest on."""
    return tuple(item.output for item in chat_ctx.items if isinstance(item, FunctionCallOutput))
