"""Where the org's alerts are posted: its webhook, set, read, taken back, and proven once."""

from fastapi import APIRouter

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.webhook import Webhook
from pinecall.gateway import _alerts
from pinecall.gateway._deps import GatewayDep, ProvidersKey, ScopeDep
from pinecall.tenancy import webhooks
from pinecall.wire.rest.webhooks import WebhookRequest, WebhookResponse, WebhookTestResponse

router = APIRouter(tags=["webhooks"])

NOWHERE = "this org posts its alerts nowhere: PUT /v1/webhook names a URL"
A_TEST_SAYS = {"said": "Pinecall reaches this URL; every alert of the org comes the same way."}


@router.get("/v1/webhook")
async def where_alerts_go(key: ProvidersKey, gateway: GatewayDep) -> WebhookResponse | None:
    """The org's webhook and whether its posts are signed, never the secret; null when none."""
    connections = gateway.connections
    found = await webhooks.webhook_of(connections.pool, connections.vault, key.org)
    if found is None:
        return None
    return WebhookResponse(url=found.url, signed=found.secret is not None)


@router.put("/v1/webhook", status_code=204)
async def post_alerts_to(body: WebhookRequest, key: ProvidersKey, gateway: GatewayDep) -> None:
    """Keep the org's webhook; its alerts are posted there from the next one."""
    wanted = Webhook(url=body.url.strip(), secret=body.secret or None)
    connections = gateway.connections
    await webhooks.put_webhook(connections.pool, connections.vault, key.org, wanted)


@router.delete("/v1/webhook", status_code=204)
async def stop_posting_alerts(key: ProvidersKey, gateway: GatewayDep) -> None:
    """Forget the org's webhook; its alerts stay on the agents' logs from the next one."""
    if not await webhooks.drop_webhook(gateway.connections.pool, key.org):
        raise NotFound(NOWHERE)


# The one webhook door that waits on the URL: a person watches whether it answered.
@router.post("/v1/webhook/test")
async def post_a_test(
    key: ProvidersKey, scope: ScopeDep, gateway: GatewayDep
) -> WebhookTestResponse:
    """One test post, signed as every alert is, waited for: whether a 2xx came back."""
    connections = gateway.connections
    found = await webhooks.webhook_of(connections.pool, connections.vault, key.org)
    if found is None:
        raise Conflict(NOWHERE)
    sent = _alerts.payload(scope, None, _alerts.A_TEST, dict(A_TEST_SAYS))
    error = await _alerts.posted(connections.http, found, sent)
    return WebhookTestResponse(sent=error is None, error=error)
