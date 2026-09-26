"""The brand the box's letters carry, and later its sign-in page: read and set by the operator."""

from __future__ import annotations

from pinecall.api.ops.box_settings import BoxSettingsDep
from pinecall.api.scope.operator_key import an_operators_router
from pinecall.mail import rebranded, the_brand
from pinecall.orgs.box import BRAND
from pinecall_protocol import WireModel
from pinecall_protocol.rest import BoxBrand

# The same gate every /v1/ops door takes. A brand is the box's and nobody else's: what a letter
# is signed as is decided by whoever runs the machine the letter leaves from.
operator = an_operators_router()


class Rebranding(WireModel):
    """What may change. A field left out keeps what it had; an empty one goes back to default."""

    name: str | None = None
    # https only, an image: a mail client fetches it from wherever the reader is. Empty clears it.
    logo_url: str | None = None
    # #rrggbb, six hex digits: it is written into a style attribute of every letter.
    accent: str | None = None


@operator.get("/brand")
async def branded(box: BoxSettingsDep) -> BoxBrand:
    """What the letters are called and painted with: Pinecall, its accent and no logo until set."""
    return BoxBrand.model_validate((await the_brand(box)).as_json)


@operator.put("/brand")
async def rebrand(said: Rebranding, box: BoxSettingsDep) -> BoxBrand:
    """The brand with these fields replaced, kept, and answered whole; 400 in a sentence."""
    wanted = rebranded(await the_brand(box), said.name, said.logo_url, said.accent)
    await box.put(BRAND, wanted.as_json)
    return BoxBrand.model_validate(wanted.as_json)
