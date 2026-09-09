"""A contact's memory, as the tenant reads it and as the contact has the right to erase it."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api._deps import KeptMemoryDep, KeyDep
from pinecall.types import Fact
from pinecall_protocol.rest import ContactFact, ContactMemory, Forgotten

router = APIRouter()


# The history, not the current facts: every row memory ever held about this contact, the current
# ones first, then what they superseded — with the two dates that bound each. What the marker
# reads per turn is `recall`; this is what a person reads when the contact asks what is known.
@router.get("/v1/contacts/{contact}/memory")
async def history(contact: str, key: KeyDep, memory: KeptMemoryDep) -> ContactMemory:
    """Everything memory ever kept about one contact of this org, current facts first."""
    facts = await memory.history(key.org, contact)
    return ContactMemory(facts=[_on_the_wire(fact) for fact in facts])


# The one DELETE memory has: every row of the contact at once, superseded ones included, because
# the right to be forgotten is not the right to have the current version forgotten.
@router.delete("/v1/contacts/{contact}/memory")
async def forget(contact: str, key: KeyDep, memory: KeptMemoryDep) -> Forgotten:
    """Every fact of the contact, gone; how many went. Zero is a fine answer, not a 404."""
    return Forgotten(forgotten=await memory.forget(key.org, contact))


def _on_the_wire(fact: Fact) -> ContactFact:
    """One row as the door says it: the fact, and since when and until when it held."""
    return ContactFact(
        id=fact.id,
        text=fact.text,
        category=fact.category,
        source=fact.source,
        valid_from=fact.valid_from.timestamp(),
        invalidated_at=None if fact.invalidated_at is None else fact.invalidated_at.timestamp(),
    )
