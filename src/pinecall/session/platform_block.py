"""The one prompt block the platform writes: the file a class ships with, and its log line."""

from __future__ import annotations

from pinecall.log import hashed_prompt
from pinecall.session.pending_tools import Emit
from pinecall.types import KNOWLEDGE, AgentConfig, Blocks
from pinecall_protocol.events import PromptChanged


# The app sends a prompt.set for every block it renders, and none for this one: the file travels
# whole in the declaration and the platform reads it into the block. Without a line here the log
# would list identity, tools and the view, and a reader would conclude the file reached nobody —
# which is the conclusion a live call led its own author to on 2026-09-10, wrongly.
async def a_line_for_the_file_it_ships_with(blocks: Blocks, emit: Emit) -> None:
    """prompt.changed for the knowledge block, so the log says what the model actually reads."""
    text = blocks.text_of(KNOWLEDGE)
    if not text:
        return
    await emit(
        "prompt.changed", PromptChanged(name=KNOWLEDGE, hash=hashed_prompt(text), chars=len(text))
    )


# The class's own file, as the declaration carried it. A class that ships none has an empty
# knowledge block, which sends nothing at all.
def the_file_it_ships_with(config: AgentConfig) -> str:
    """The text of the file this agent knows by heart, or nothing."""
    return config.knowledge or ""
