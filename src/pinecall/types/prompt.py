"""The prompt as named blocks in two regions: the layout an agent declares, and what each holds."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pinecall.types.refused import DeclarationRefused

# Where a block sits: before the history, cached by the provider; or after it, replaced every turn.
type PromptRegion = Literal["static", "dynamic"]

# The static blocks reach livekit as its one `instructions` string, joined by this: the default
# layout's joined text is byte for byte the prefix an app used to send whole.
BETWEEN_BLOCKS = "\n\n"


@dataclass(frozen=True)
class PromptBlock:
    """One named block of the prompt, and the region it lives in."""

    name: str
    region: PromptRegion


# The prompt when the app declares nothing: who the agent is, what it knows and what it may call,
# then the history, then its view of the state — the view last, because it is the last thing read.
# The block the file a class ships with is read into, written by the platform and never by
# the app, so a knowledge file reaches the model as the operator's own words.
KNOWLEDGE = "knowledge"

DEFAULT_LAYOUT: tuple[PromptBlock, ...] = (
    PromptBlock("identity", "static"),
    PromptBlock("knowledge", "static"),
    PromptBlock("tools", "static"),
    PromptBlock("view", "dynamic"),
)


# One per call. The app writes a block by name with prompt.set; the session reads the static text
# for livekit's instructions and the dynamic texts for the end of every request. Nothing here knows
# a framework or a vendor: how the blocks reach a model is providers/blocks.py.
class Blocks:
    """One call's prompt: the blocks in the order they are sent, and the text each holds now."""

    # The `knowledge` block is the one the platform writes and the app never does: the file's text
    # travels whole in the declaration, and putting it here is what makes it the operator's words
    # in the cached prefix. docs/security/prompt-injection.md, the second row of the table.
    def __init__(self, layout: Sequence[PromptBlock] = DEFAULT_LAYOUT, knowledge: str = "") -> None:
        self._layout = tuple(layout)
        self._texts: dict[str, str] = {block.name: "" for block in self._layout}
        if knowledge and KNOWLEDGE in self._texts:
            self._texts[KNOWLEDGE] = knowledge

    def text_of(self, name: str) -> str:
        """What the app last wrote into one block; empty until it writes."""
        self._declared(name)
        return self._texts[name]

    # Rewriting the instructions with the same bytes still costs the provider a cache write, so the
    # caller is told whether the static text moved at all, and touches livekit only when it did.
    def set(self, name: str, text: str) -> bool:
        """Rewrite one block whole. True when the static text the provider caches changed."""
        self._declared(name)
        before = self.instructions
        self._texts[name] = text
        return self.instructions != before

    @property
    def instructions(self) -> str:
        """Every static block that holds text, joined: livekit's one instructions string."""
        return BETWEEN_BLOCKS.join(self.static_texts)

    @property
    def static_texts(self) -> tuple[str, ...]:
        """The static blocks that hold text, in layout order, each on its own."""
        return self._texts_in("static")

    @property
    def dynamic_texts(self) -> tuple[str, ...]:
        """The dynamic blocks that hold text, in layout order, each on its own."""
        return self._texts_in("dynamic")

    def _texts_in(self, region: PromptRegion) -> tuple[str, ...]:
        """One region's texts, in layout order; a block nobody wrote is not sent at all."""
        return tuple(
            self._texts[block.name]
            for block in self._layout
            if block.region == region and self._texts[block.name]
        )

    def _declared(self, name: str) -> None:
        """A name outside the layout is refused with the names that are in it."""
        if name not in self._texts:
            raise DeclarationRefused(
                f"the prompt has no block named {name!r}: "
                f"this agent's blocks are {', '.join(self._texts)}"
            )
