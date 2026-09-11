"""The prompt's blocks as one request: the static ones kept apart, the view last of all."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast, override

from livekit.agents import llm as agents

from pinecall.types import Blocks


# The Anthropic plugin formats whatever context it is handed (plugins/anthropic/llm.py:222) and
# sends one `system` text block per string in `system_messages`, with its cache breakpoint on the
# last of them (:234). Anthropic checks a prefix back across block boundaries, so three blocks
# beat one: a `tools` block rewritten leaves `identity` and `knowledge` read from the cache. The
# formatter itself keeps only the FIRST system item as the preamble (_provider_format/utils.py:49),
# so the blocks travel joined, in livekit's one instructions item, and are split again here and
# nowhere else. Every other provider reads the joined string; the order alone gives it the same
# win. See docs/decisions/prompt-blocks.md.
class SystemBlocks(agents.ChatContext):
    """livekit's ChatContext, with the static blocks kept apart for the provider that uses them."""

    def __init__(self, items: list[agents.ChatItem], static: Sequence[str]) -> None:
        super().__init__(items)
        self._static = tuple(static)

    @override
    def to_provider_format(  # pyright: ignore[reportIncompatibleMethodOverride] — the base is overloaded per provider
        self, format: str, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], Any]:
        """The base formatter's request; for Anthropic, the system list is one string per block."""
        # The base's return is generic enough that a strict checker cannot read it; one cast here.
        messages, data = cast(
            "tuple[list[dict[str, Any]], Any]",
            super().to_provider_format(format, **kwargs),  # pyright: ignore[reportUnknownMemberType]
        )
        if format == "anthropic" and self._static:
            data.system_messages = list(self._static)
        return messages, data


# livekit hands llm_node a copy of the history and reuses it across the tool steps of one turn, so
# what this adds goes onto a NEW context and never into that copy: each request ends with exactly
# one of each, and none of it piles up behind a tool's output.
#
# The order is the one the security page fixes and nothing may reorder it: the static blocks, the
# history with this turn's lookups inside it — each a real tool_use / tool_result pair, which is
# where everything from outside the conversation goes — and last the tenant's own view, which is
# the only thing in the request that carries operator authority after the system field.
# docs/security/prompt-injection.md.
def request_context(
    chat_ctx: agents.ChatContext, blocks: Blocks, lookups: Sequence[agents.ChatItem] = ()
) -> SystemBlocks:
    """One request: the history, this turn's lookups before the caller, then the dynamic blocks."""
    items = _ahead_of_the_caller(chat_ctx.items, lookups)
    items.extend(agents.ChatMessage(role="system", content=[text]) for text in blocks.dynamic_texts)
    return SystemBlocks(items, blocks.static_texts)


# A lookup's pair goes BEFORE the caller's newest words, which is where every framework that does
# this puts it. livekit hands `on_user_turn_completed` a context that does not hold the new message
# yet and appends it after the hook returns (agent_activity.py:2599-2606, then :2672 hands that
# very context to the reply), and its own RAG example adds the retrieved text there; Pipecat's Mem0
# service inserts memories near the head of the list; convo uses ChatContext.insert, which places
# by created_at. This runtime appended them AFTER instead, and nothing said why.
#
# What that cost: the caller's sentence ended up several messages back from where the model
# decides, behind two tool_results, with the view the last thing read. Measured 2026-09-11 by
# replaying clinica-norte's own recorded requests — 0 of 8 on the golden's expected tool, and 8 of
# 8 once the caller's words sat next to the view. It is also what Anthropic describes: text placed
# after tool results reads as the end of the tool-using turn.
#
# The pair still lives only in the REQUEST and never in the history. livekit keeps that by handing
# the hook a throwaway copy; this runtime cannot, because the text session hands its hook the real
# chat_ctx (session/text/turns.py) and anything written there would pile up turn after turn. Same
# contract, kept by splicing here instead of by copying there.
def _ahead_of_the_caller(
    items: Sequence[agents.ChatItem], lookups: Sequence[agents.ChatItem]
) -> list[agents.ChatItem]:
    """The history with this turn's lookups spliced in ahead of the caller's newest message."""
    if not lookups:
        return list(items)
    # Only when the caller's own turn is the last thing on the context. A tool step of a turn
    # already under way ends in a tool output, and `agent.reply` in nothing the caller said: there
    # is no newest message to sit ahead of, so the pair goes where it always went.
    last = items[-1] if items else None
    if not (isinstance(last, agents.ChatMessage) and last.role == "user"):
        return [*items, *lookups]
    return [*items[:-1], *lookups, last]


# What `prompt.changed` deliberately does not carry: a live call keeps a hash of each block and
# nothing else, because the log travels and a prompt holds the caller's own words. A run that is
# being reproduced is the one reader that needs the text, and it asks for it here, by hand.
def as_a_request(request: SystemBlocks, vendor: str) -> dict[str, Any]:
    """One request as the vendor's own formatter builds it: the system blocks, then the messages."""
    messages, extra = request.to_provider_format(vendor)
    # Anthropic is the one format that carries the blocks apart (see the class above); every other
    # vendor's system text is already inside `messages`, and an empty list here says exactly that.
    return {"system": list(getattr(extra, "system_messages", ()) or ()), "messages": messages}
