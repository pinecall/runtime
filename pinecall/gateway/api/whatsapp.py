"""Meta's webhook: the handshake that subscribes it, and the messages it delivers."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from pinecall.channels import whatsapp
from pinecall.domain.errors import NotAllowed, NotAvailable
from pinecall.gateway._deps import GatewayDep, webhook_body

logger = logging.getLogger(__name__)

router = APIRouter()

UNSIGNED = "the body carries no signature of this box's Meta app"

STILL_READING = "{count} of these messages are being read by another delivery: send it again"


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


# Past the signature the answer is 200: Meta disables a webhook that keeps failing, so what goes
# wrong after it is a line in the log. The one exception is a message another delivery is still
# reading: 503 keeps it on Meta's side until that reading is done or has died.
@router.post("/v1/whatsapp/webhook")
async def receive_webhook(request: Request, gateway: GatewayDep) -> dict[str, int]:
    """Every message of a signed body onto its contact's conversation."""
    app = await whatsapp.box_account(gateway.connections.pool, gateway.connections.vault)
    body = await webhook_body(request)
    if not whatsapp.is_signed(app.app_secret, body, request.headers.get(whatsapp.SIGNATURE_HEADER)):
        raise NotAllowed(UNSIGNED)
    messages = whatsapp.messages_in(body)
    unread = [inbound for inbound in messages if not await gateway.threads.received(inbound)]
    if unread:
        raise NotAvailable(STILL_READING.format(count=len(unread)))
    return {"received": len(messages)}
