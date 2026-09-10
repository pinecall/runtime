"""Memory, the Protocol: what a contact's calls taught, recalled per turn, written at hang-up."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pinecall.types import Channel, Fact, MemoryPolicy, Model, ProviderKeys, ToolSpec
from pinecall_protocol.defs import MemoryOp

# How many facts one recall hands the model: six, which reads as what it knows about a person
# and not as a list.
DEFAULT_FACTS_PER_TURN = 6


# A turn as memory reads it back off the log: who spoke, and the words. Tool payloads never reach
# the model that extracts, which is why this is not the log's entry.
@dataclass(frozen=True)
class Spoken:
    """One turn of the call as the extractor reads it: the caller's words, or the agent's."""

    role: Literal["user", "agent"]
    text: str


class Memory(Protocol):
    """The contact's facts: recalled under a turn's budget, written at hang-up, erased on ask."""

    async def recall(
        self,
        org: str,
        contact: str,
        query: str,
        *,
        k: int = DEFAULT_FACTS_PER_TURN,
        as_of: datetime | None = None,
    ) -> list[Fact]:
        """The facts that answer the caller's words best, scored 0..1, in 150 ms and no model."""
        ...

    # The org's model, with the org's keys, exactly as the session builds the one the agent talks
    # with: the caller hands both in, and this Protocol never reaches for the settings.
    async def remember(
        self,
        org: str,
        contact: str,
        turns: Sequence[Spoken],
        *,
        channel: Channel,
        at: datetime,
        policy: MemoryPolicy,
        llm: Model | None,
        keys: ProviderKeys,
        call: str | None = None,
        tools: Sequence[ToolSpec] = (),
    ) -> list[MemoryOp]:
        """One model call over the call's turns; what it taught, as rows added and superseded."""
        ...

    async def forget(self, org: str, contact: str) -> int:
        """Every row of the contact, gone — the right to be forgotten. How many went."""
        ...

    # What the memory_facts quota is measured against. A count of rows and never a counter
    # column: the rows are the truth and a number kept beside them is a second one that drifts.
    async def kept(self, org: str) -> int:
        """How many facts this org holds right now, across every contact it has ever met."""
        ...

    async def history(self, org: str, contact: str) -> list[Fact]:
        """Every fact ever held about the contact: the current ones first, then the superseded."""
        ...
