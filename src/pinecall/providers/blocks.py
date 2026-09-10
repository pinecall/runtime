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
# history, then what this turn's lookups found — each a real tool_use / tool_result pair, which is
# where everything from outside the conversation goes — and last the tenant's own view, which is
# the only thing in the request that carries operator authority after the system field.
# docs/security/prompt-injection.md.
def request_context(
    chat_ctx: agents.ChatContext, blocks: Blocks, lookups: Sequence[agents.ChatItem] = ()
) -> SystemBlocks:
    """One request: the history livekit built, this turn's lookups, then the dynamic blocks."""
    items = [*chat_ctx.items, *lookups]
    items.extend(agents.ChatMessage(role="system", content=[text]) for text in blocks.dynamic_texts)
    return SystemBlocks(items, blocks.static_texts)
