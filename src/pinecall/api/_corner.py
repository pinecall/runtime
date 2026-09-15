"""The dependency the worker's doors take beside their scope: whose corner this request is in."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query

from pinecall.api._deps import KeyDep
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
