"""The prompt's blocks as one request: the static ones kept apart, the view last of all."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast, override

from livekit.agents import llm as agents

from pinecall.types import Blocks


# The Anthropic plugin sends one `system` text block per string in `system_messages`, with its
# cache breakpoint on the last (plugins/anthropic/llm.py:222-234), and a prefix is checked back
# across block boundaries: a `tools` block rewritten leaves `identity` and `knowledge` cached. The
# formatter keeps only the FIRST system item (_provider_format/utils.py:49), so the blocks travel
# joined in livekit's instructions item and are split again here. docs/decisions/prompt-blocks.md.
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
        messages, data = cast(
            "tuple[list[dict[str, Any]], Any]",
            super().to_provider_format(format, **kwargs),  # pyright: ignore[reportUnknownMemberType]
        )
        if format == "anthropic" and self._static:
            data.system_messages = list(self._static)
        return messages, data


# Built on a NEW context every time: livekit reuses its copy of the history across the tool steps
# of one turn, so anything added to it would pile up behind a tool's output. The order is the one
# the security page fixes: the static blocks, the history with this turn's lookups inside it as
# real tool_use / tool_result pairs, and last the tenant's view. docs/security/prompt-injection.md.
def request_context(
    chat_ctx: agents.ChatContext, blocks: Blocks, lookups: Sequence[agents.ChatItem] = ()
) -> SystemBlocks:
    """One request: the history, this turn's lookups before the caller, then the dynamic blocks."""
    items = _ahead_of_the_caller(chat_ctx.items, lookups)
    items.extend(agents.ChatMessage(role="system", content=[text]) for text in blocks.dynamic_texts)
    return SystemBlocks(items, blocks.static_texts)


# A lookup's pair goes BEFORE the caller's newest words, where livekit's own RAG example and
# Pipecat's memory service put it: livekit appends the new message only after
# `on_user_turn_completed` returns (agent_activity.py:2599-2606). Appended AFTER instead, the
# caller's sentence sat behind two tool_results with the view last, and the model read it as the
# end of a tool-using turn: 0 of 8 on the expected tool, 8 of 8 once it sat next to the view
# (2026-09-11, clinica-norte's recorded requests). The pair lives in the request and never in the
# history — kept by splicing here, because the text session hands its hook the real chat_ctx.
def _ahead_of_the_caller(
    items: Sequence[agents.ChatItem], lookups: Sequence[agents.ChatItem]
) -> list[agents.ChatItem]:
    """The history with this turn's lookups spliced in ahead of the caller's newest message."""
    if not lookups:
        return list(items)
    # A tool step under way ends in a tool output, and `agent.reply` in nothing the caller said:
    # with no newest message to sit ahead of, the pair goes at the end.
    last = items[-1] if items else None
    if not (isinstance(last, agents.ChatMessage) and last.role == "user"):
        return [*items, *lookups]
    return [*items[:-1], *lookups, last]


# A live call's log keeps a hash of each block and never the text, because the log travels and a
# prompt holds the caller's own words. A run being reproduced is the one reader that asks for it.
def vendor_request(request: SystemBlocks, vendor: str) -> dict[str, Any]:
    """One request as the vendor's own formatter builds it: the system blocks, then the messages."""
    messages, extra = request.to_provider_format(vendor)
    # Anthropic carries the blocks apart; every other vendor's system text is already in `messages`.
    return {"system": list(getattr(extra, "system_messages", ()) or ()), "messages": messages}
