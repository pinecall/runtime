"""The two routes Meta knocks at: the subscription handshake, and every message it delivers."""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from starlette.responses import PlainTextResponse

from pinecall.api.deps import SettingsDep
from pinecall.api.whatsapp.thread_deps import DoorsDep
from pinecall.api.whatsapp.threads import ThreadsDep
from pinecall.whatsapp.inbound_message import Inbound, Payload, messages_in
from pinecall.whatsapp.webhook_signature import SIGNATURE_HEADER, signed
from pinecall_protocol import WireModel

logger = logging.getLogger(__name__)
router = APIRouter()


class WebhookReceived(WireModel):
    """What Meta is told back: how many messages the body carried onto a thread. Never a 4xx."""

    received: int


# What a runtime with no PINECALL_WHATSAPP_APP_SECRET answers. A 503 and not a 404: the request
# was right and this box cannot honour it — the same shape the vault's NO_VAULT_KEY has.
NO_WHATSAPP = "no PINECALL_WHATSAPP_APP_SECRET: this runtime answers no WhatsApp"

# The handshake was not the one this box is waiting for: a wrong verify token, or a mode that is
# not `subscribe`. 403 and no words: whoever knocked is not Meta setting our webhook up.
NOT_THE_HANDSHAKE = "that is not this runtime's verify token"

# A signature that is missing, malformed or simply wrong. The body is not read at all.
NOT_META = "this body was not signed with the app secret"

# A body that parsed into no messages at all: a delivery receipt, a template status, a shape Meta
# added last week. It is not an error, and the line is what tells an operator that.
NOTHING_TO_READ = "whatsapp: a body with no messages in it (%s)"


# Meta's subscription handshake: it GETs once, with a challenge, and keeps the webhook only if the
# exact challenge comes back as text. See developers.facebook.com/docs/graph-api/webhooks.
@router.get("/v1/whatsapp/webhook", response_class=PlainTextResponse)
async def verify(
    settings: SettingsDep,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> PlainTextResponse:
    """Echo Meta's challenge back, when it came with the word this box is waiting for."""
    if not settings.whatsapp_app_secret:
        raise HTTPException(503, NO_WHATSAPP)
    if mode != "subscribe" or not hmac.compare_digest(token, settings.whatsapp_verify_token or ""):
        raise HTTPException(403, NOT_THE_HANDSHAKE)
    return PlainTextResponse(challenge)


# No API key: Meta is the caller and the signature IS the authentication. Everything past it
# answers 200, whatever the body turned out to be — Meta disables a webhook that keeps failing,
# and a shape this door does not understand is a line in the log, never a 4xx.
@router.post("/v1/whatsapp/webhook")
async def delivered(
    request: Request, settings: SettingsDep, threads: ThreadsDep, doors: DoorsDep
) -> WebhookReceived:
    """Every message in this body onto its own thread, and 200 as soon as they are queued."""
    if not settings.whatsapp_app_secret:
        raise HTTPException(503, NO_WHATSAPP)
    # The RAW body, before anything parses it: JSON round-tripped through Python is not the bytes
    # Meta hashed, and re-encoding it would fail every signature this door will ever be sent.
    body = await request.body()
    if not signed(settings.whatsapp_app_secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(403, NOT_META)
    inbound = _messages(body)
    for message in inbound:
        await threads.received(doors, message)
    return WebhookReceived(received=len(inbound))


def _messages(body: bytes) -> tuple[Inbound, ...]:
    """The messages this body carries, or none at all and one line saying the body had none."""
    try:
        payload = Payload.model_validate_json(body)
    except ValidationError as unreadable:
        logger.warning(NOTHING_TO_READ, unreadable)
        return ()
    return messages_in(payload)
