"""The brand the box's letters carry, and later its sign-in page: read and set by the operator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pinecall.api._box import BoxSettingsDep
from pinecall.api._operator import an_operator
from pinecall.mail import rebranded, the_brand
from pinecall.orgs.box import BRAND
from pinecall.types import DeclarationRefused
from pinecall_protocol import WireModel

# The same gate every /v1/ops door takes. A brand is the box's and nobody else's: what a letter
# is signed as is decided by whoever runs the machine the letter leaves from.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])


class Rebranding(WireModel):
    """What may change. A field left out keeps what it had; an empty one goes back to default."""

    name: str | None = None
    # https only, an image: a mail client fetches it from wherever the reader is. Empty clears it.
    logo_url: str | None = None
    # #rrggbb, six hex digits: it is written into a style attribute of every letter.
    accent: str | None = None


@operator.get("/brand")
async def branded(box: BoxSettingsDep) -> dict[str, Any]:
    """What the letters are called and painted with: Pinecall, its accent and no logo until set."""
    return (await the_brand(box)).as_json


@operator.put("/brand")
async def rebrand(said: Rebranding, box: BoxSettingsDep) -> dict[str, Any]:
    """The brand with these fields replaced, kept, and answered whole; 400 in a sentence."""
    try:
        wanted = rebranded(await the_brand(box), said.name, said.logo_url, said.accent)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    await box.put(BRAND, wanted.as_json)
    return wanted.as_json
