"""Memory, the Protocol: what a contact's calls taught, recalled per turn, written at hang-up."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pinecall.types import Channel, Env, Fact, MemoryPolicy, Model, ProviderKeys, ToolSpec
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

    # A contact's facts are one WORLD's, as the call that taught them was: what a test call on a
    # laptop learns about a number never reaches the memory a production call reads under that
    # same number, and the other way round. Every read and write says which; `kept` alone reads
    # both, because a quota is about rows on a disk. 0018 is where the column went in.
    #
    # And one CORNER's (0021): three developers of one tenant test against the same numbers, and
    # before this the fact one of them planted arrived in another's call. `holder` is the member
    # the key names, or None for the org's own — production's always, and CI's. Unlike a knowledge
    # base there is NO fallback: a base is something somebody wrote down for the agent to read,
    # and a fact is what a CALL learned. There is no org-wide sandbox call to inherit from.
    async def recall(
        self,
        org: str,
        env: Env,
        holder: str | None,
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
        env: Env,
        holder: str | None,
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

    # The other way facts are written, and the only one with no model in it: the sentences are
    # given, not extracted. A golden is what asks for it — it brings the facts a question's contact
    # holds and needs no such contact to exist — and the write is a real one, so what a golden then
    # measures is the ranking a call would get and not an arithmetic of its own.
    async def hold(
        self,
        org: str,
        env: Env,
        holder: str | None,
        contact: str,
        facts: Sequence[str],
        *,
        at: datetime,
    ) -> None:
        """These sentences as the contact's facts, embedded and written; no model is asked."""
        ...

    async def forget(self, org: str, env: Env, holder: str | None, contact: str) -> int:
        """Every row of the contact, gone — the right to be forgotten. How many went."""
        ...

    # What the memory_facts quota is measured against. A count of rows and never a counter
    # column: the rows are the truth and a number kept beside them is a second one that drifts.
    async def kept(self, org: str) -> int:
        """How many facts this org holds right now, every contact and both worlds together."""
        ...

    async def history(self, org: str, env: Env, holder: str | None, contact: str) -> list[Fact]:
        """Every fact ever held about the contact: the current ones first, then the superseded."""
        ...

    # A fact carries the call that taught it, and that call the agent that took it: so what an
    # agent's calls taught, across every contact, is a read and not a column. A fact a golden held
    # came from no call and belongs to no agent's list.
    async def taught_by(
        self,
        org: str,
        env: Env,
        holder: str | None,
        agent: str,
        *,
        words: str | None,
        after: str | None,
        limit: int,
    ) -> FactsPage:
        """The current facts this agent's calls taught, newest first, a page after the cursor."""
        ...

    # Forgetting ONE fact is the bi-temporal end a later call would have written: the row stays,
    # with the moment it stopped holding, and recall stops reading it from that moment.
    async def invalidated(
        self, org: str, env: Env, holder: str | None, id: str, at: datetime
    ) -> bool:
        """This current fact holds no more from `at`. False when no current fact answers the id."""
        ...


@dataclass(frozen=True)
class FactsPage:
    """One page of facts and the cursor the next one starts after, or None on the last."""

    facts: list[Fact]
    next: str | None
