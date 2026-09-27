"""Meta's webhook: the handshake that subscribes it, and the messages it delivers."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from pinecall.channels import whatsapp
from pinecall.domain.errors import NotAllowed
from pinecall.gateway.deps import WiredDep

logger = logging.getLogger(__name__)

router = APIRouter()

UNSIGNED = "the body carries no signature of this box's Meta app"


@router.get("/v1/whatsapp/webhook", response_class=PlainTextResponse)
async def handshake(
    box: WiredDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    word: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """Meta subscribing: its challenge echoed back when it says this box's word."""
    meta = await whatsapp.the_boxs(box.pool, box.vault)
    return whatsapp.handshake(meta, mode, word, challenge)


# Past the signature every answer is 200: Meta disables a webhook that keeps failing, so what
# goes wrong after it is a line in the log.
@router.post("/v1/whatsapp/webhook")
async def delivered(request: Request, box: WiredDep) -> dict[str, int]:
    """Every message of a signed body onto its contact's conversation."""
    meta = await whatsapp.the_boxs(box.pool, box.vault)
    body = await request.body()
    if not whatsapp.is_signed(
        meta.app_secret, body, request.headers.get(whatsapp.SIGNATURE_HEADER)
    ):
        raise NotAllowed(UNSIGNED)
    messages = whatsapp.messages_in(body)
    for inbound in messages:
        await box.threads.received(inbound)
    return {"received": len(messages)}
