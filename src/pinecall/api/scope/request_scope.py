"""The dependency the worker's doors take beside their scope: whose corner this request is in."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query

from pinecall.api.agents.held_agent import Registration
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.api.deps import KeyDep
from pinecall.auth.corner import Corner, corner_of
from pinecall.types import DeclarationRefused, Env


# A tenant's key resolves in its own corner and is refused any other, in keys.md's words; the
# fleet's key — the box's one worker, serving every org — names the corner of the CALL it is
# answering, `?org=&env=&holder=`, as the dispatch said them. The scope is asked by the door's
# own dep beside this one; this only says WHERE. Nothing named is the key's own corner, so every
# door reads exactly as it did for every key that names none.
async def a_corner(
    key: KeyDep,
    org: Annotated[str | None, Query()] = None,
    env: Annotated[Env | None, Query()] = None,
    holder: Annotated[str | None, Query()] = None,
) -> Corner:
    """The org, the world and the holder this request resolves in: the key's, or the call's."""
    try:
        return corner_of(key, org, env, holder)
    except DeclarationRefused as refused:
        raise HTTPException(403, str(refused)) from refused


CornerDep = Annotated[Corner, Depends(a_corner)]


# "The agent in this corner, or 404" was nine doors' own three lines. An API key IS its org, so a
# slug another org holds is a 404 here — that it exists at all is not the asker's business — and
# a slug nobody holds is the same 404: what is turned on an agent nobody holds is nothing.
def an_agent_held(slug: str, corner: CornerDep, registry: RegistryDep) -> Registration:
    """The socket holding this agent in this corner: what it declared, and its doors."""
    held = registry.of(corner.env, slug, corner.holder)
    if held is None or held.org != corner.org:
        raise HTTPException(404, NO_AGENT.format(slug=slug))
    return held


HeldDep = Annotated[Registration, Depends(an_agent_held)]
# For a door that asks only that somebody holds it, and reads nothing of what they declared.
AnAgentHeld = Depends(an_agent_held)
