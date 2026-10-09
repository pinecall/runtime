"""/v1/webhook: where the org's alerts are posted, and a post to prove the URL answers."""

from pinecall.wire.frames import WireModel


class WebhookRequest(WireModel):
    """PUT /v1/webhook: the URL, and the secret every post is signed with (never read back)."""

    url: str
    secret: str | None = None


class WebhookResponse(WireModel):
    """GET /v1/webhook: the URL and whether posts are signed; null when alerts go nowhere."""

    url: str
    signed: bool


class WebhookTestResponse(WireModel):
    """POST /v1/webhook/test, the answer: whether the URL took it, and what went wrong if not."""

    sent: bool
    error: str | None
