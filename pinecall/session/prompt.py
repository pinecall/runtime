"""A call's prompt: its blocks, the request each turn sends, its greeting, its knowledge entry."""

from collections.abc import Sequence
from hashlib import sha256
from typing import override

from livekit.agents import llm

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.types import DEFAULT_LAYOUT, KNOWLEDGE, Greeting, PromptBlock, PromptRegion
from pinecall.wire.events import PromptChanged

# Joins the static blocks into livekit's one `instructions` string.
BETWEEN_BLOCKS = "\n\n"


class Blocks:
    """One call's prompt blocks, in the order they are sent, and their text now."""

    # The knowledge is set here and never by the app, so it lands in the cached prefix as the
    # operator's text (docs/security/prompt-injection.md).
    def __init__(self, layout: Sequence[PromptBlock] = DEFAULT_LAYOUT, knowledge: str = "") -> None:
        """Every block of the layout, empty but the knowledge."""
        self.layout = tuple(layout)
        self.texts: dict[str, str] = {block.name: "" for block in self.layout}
        if knowledge and KNOWLEDGE in self.texts:
            self.texts[KNOWLEDGE] = knowledge

    # Identical static text is not resent: even the same bytes cost the provider a cache write.
    def set(self, name: str, text: str) -> bool:
        """Replace a block's text; True when the static instructions changed."""
        if name not in self.texts:
            raise DeclarationRefused(
                f"the prompt has no block named {name!r}: "
                f"this agent's blocks are {', '.join(self.texts)}"
            )
        before = self.instructions
        self.texts[name] = text
        return self.instructions != before

    @property
    def instructions(self) -> str:
        """The static blocks written so far, joined, in layout order."""
        return BETWEEN_BLOCKS.join(self.of("static"))

    def of(self, region: PromptRegion) -> tuple[str, ...]:
        """A region's blocks that have text, in layout order."""
        return tuple(
            self.texts[block.name]
            for block in self.layout
            if block.region == region and self.texts[block.name]
        )


class Request(llm.ChatContext):
    """One turn's request, whose static blocks reach the provider one system block each."""

    def __init__(self, items: list[llm.ChatItem], static: tuple[str, ...]) -> None:
        """The request's items, and the static blocks it keeps apart."""
        super().__init__(items)
        self.static = static

    # livekit's formatters keep only the first system item as the preamble
    # (_provider_format/utils.py); a format that carries system messages apart (the one that
    # caches up to its last block among them) gets one per static block instead.
    @override
    def to_provider_format(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, format: str, **kwargs: object
    ) -> tuple[list[dict[str, object]], object]:
        """The request as the provider's formatter builds it, the static blocks kept apart."""
        formatted = super().to_provider_format(format, **kwargs)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if self.static and hasattr(formatted[1], "system_messages"):
            formatted[1].system_messages = list(self.static)
        return formatted  # pyright: ignore[reportUnknownVariableType]


# Always a new context: livekit reuses its own across a turn's tool steps, so additions would
# pile up. The order is the one prompt-injection.md states: static blocks, history, the view.
def request(
    history: llm.ChatContext, blocks: Blocks, lookups: Sequence[llm.ChatItem] = ()
) -> Request:
    """One request: the history with this turn's lookups spliced in, then the dynamic blocks."""
    items = _ahead_of_the_caller(history.items, lookups)
    items += [llm.ChatMessage(role="system", content=[text]) for text in blocks.of("dynamic")]
    return Request(items, blocks.of("static"))


# An eval run starts in the middle of a conversation, so it never greets.
def greeting_for(greeting: Greeting | None, run: str | None) -> Greeting | None:
    """The declared greeting, or none when an eval run opened the call."""
    return None if run is not None else greeting


def hashed(text: str) -> str:
    """A block's text as the log keeps it: its hash, never its words."""
    return sha256(text.encode()).hexdigest()


# The app never sends prompt.set for this block, so without this entry the log would not show
# that the file reached the model.
def knowledge_changed(blocks: Blocks) -> PromptChanged | None:
    """The entry saying the knowledge file reached the prompt, when there is one."""
    text = blocks.texts.get(KNOWLEDGE, "")
    return PromptChanged(name=KNOWLEDGE, hash=hashed(text), chars=len(text)) if text else None


# Lookups go before the caller's newest message (livekit appends it after
# on_user_turn_completed); placed after it, models skip the next tool. They are spliced into
# the request only, never into the history. No trailing user message (a tool step, agent.reply):
# they go at the end.
def _ahead_of_the_caller(
    items: Sequence[llm.ChatItem], lookups: Sequence[llm.ChatItem]
) -> list[llm.ChatItem]:
    last = items[-1] if items else None
    if not lookups or not (isinstance(last, llm.ChatMessage) and last.role == "user"):
        return [*items, *lookups]
    return [*items[:-1], *lookups, last]
