"""The prompt's blocks as one request: the static ones kept apart, the dynamic ones last."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast, override

from livekit.agents import llm as agents
from livekit.agents.voice.generation import INSTRUCTIONS_MESSAGE_ID

from pinecall.types import Blocks, filled
from pinecall.types.prompt import BETWEEN_BLOCKS


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
# the dynamic blocks go onto a NEW context and never into that copy: each request ends with exactly
# one of each, and none of them piles up behind a tool's output.
#
# The fills are applied HERE and nowhere earlier: the app's block text, and the hash prompt.changed
# carries, are what the app wrote, markers and all; what the model reads is that text with every
# marker line replaced. The knowledge fill is the same bytes on every request, so the static
# prefix stays cached; the turn's fills change after the history and cost the cache nothing.
def request_context(
    chat_ctx: agents.ChatContext, blocks: Blocks, fills: Mapping[str, str]
) -> SystemBlocks:
    """One request: the history as livekit built it, then one system message per dynamic block."""
    static = [filled(text, fills) for text in blocks.static_texts]
    items = [_instructions_filled(item, BETWEEN_BLOCKS.join(static)) for item in chat_ctx.items]
    items.extend(
        agents.ChatMessage(role="system", content=[filled(text, fills)])
        for text in blocks.dynamic_texts
    )
    return SystemBlocks(items, static)


# Every provider but Anthropic reads the static blocks off livekit's pinned instructions item, so
# that item carries the filled text: a new message under the same id, the history left as it was.
def _instructions_filled(item: agents.ChatItem, instructions: str) -> agents.ChatItem:
    """The pinned instructions item with the fills applied; any other item untouched."""
    if not isinstance(item, agents.ChatMessage) or item.id != INSTRUCTIONS_MESSAGE_ID:
        return item
    if item.text_content == instructions:
        return item
    return agents.ChatMessage(
        id=item.id, role="system", content=[instructions], created_at=item.created_at
    )
