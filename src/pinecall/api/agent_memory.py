"""Memory across callers: what an agent's calls taught every contact, and one fact forgotten."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from pinecall.api._deps import KeptMemoryDep, MemoryKeyDep
from pinecall.auth.keys import held_by
from pinecall_protocol.rest import AgentFact, AgentMemory, Forgotten

router = APIRouter()

# How many facts a screen asks for when it says nothing.
A_SCREENFUL = 50

# One sentence for a fact that is not there, one already forgotten, and another org's or world's:
# which of the three it was is not the asker's business.
NO_SUCH_FACT = "no current fact {id} in this key's org and world"


# The contact door reads ONE person's history; this reads the agent's memory whole — the current
# facts only, across every contact its calls taught, newest first — in the key's world and corner,
# as every memory door does.
@router.get("/v1/agents/{slug}/memory")
async def taught(
    slug: str,
    key: MemoryKeyDep,
    memory: KeptMemoryDep,
    after: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = A_SCREENFUL,
) -> AgentMemory:
    """The current facts this agent's calls taught, a page at a time, filtered by words."""
    page = await memory.taught_by(
        key.org, key.env, held_by(key), slug, words=q or None, after=after, limit=limit
    )
    return AgentMemory(
        facts=[
            AgentFact(
                id=fact.id,
                contact=fact.contact,
                text=fact.text,
                category=fact.category,
                written_at=fact.valid_from.timestamp(),
            )
            for fact in page.facts
        ],
        next=page.next,
    )


# Bi-temporal, as every end memory writes: the row stays with the moment it stopped holding, recall
# stops reading it, and the contact's history still shows what was known and until when. Erasing a
# person whole is DELETE /v1/contacts/{contact}/memory; this is one wrong fact.
@router.delete("/v1/memory/facts/{id}")
async def forget_one(id: UUID, key: MemoryKeyDep, memory: KeptMemoryDep) -> Forgotten:
    """This fact holds no more, from now. 404 when no current fact of this org answers the id."""
    ended = await memory.invalidated(key.org, key.env, held_by(key), str(id), datetime.now(UTC))
    if not ended:
        raise HTTPException(404, NO_SUCH_FACT.format(id=id))
    return Forgotten(forgotten=1)
