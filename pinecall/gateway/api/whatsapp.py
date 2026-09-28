"""Meta's webhook: the handshake that subscribes it, and the messages it delivers."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from pinecall.channels import whatsapp
from pinecall.domain.errors import NotAllowed
from pinecall.gateway._deps import GatewayDep

logger = logging.getLogger(__name__)

router = APIRouter()

UNSIGNED = "the body carries no signature of this box's Meta app"


@router.get("/v1/whatsapp/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    gateway: GatewayDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    word: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """Meta subscribing: its challenge echoed back when it says this box's word."""
    app = await whatsapp.box_account(gateway.connections.pool, gateway.connections.vault)
    return whatsapp.handshake(app, mode, word, challenge)


# Past the signature every answer is 200: Meta disables a webhook that keeps failing, so what
# goes wrong after it is a line in the log.
@router.post("/v1/whatsapp/webhook")
async def receive_webhook(request: Request, gateway: GatewayDep) -> dict[str, int]:
    """Every message of a signed body onto its contact's conversation."""
    app = await whatsapp.box_account(gateway.connections.pool, gateway.connections.vault)
    body = await request.body()
    if not whatsapp.is_signed(app.app_secret, body, request.headers.get(whatsapp.SIGNATURE_HEADER)):
        raise NotAllowed(UNSIGNED)
    messages = whatsapp.messages_in(body)
    for inbound in messages:
        await gateway.threads.received(inbound)
    return {"received": len(messages)}
