"""An alert of the org's: on the agent's log, then posted to the org's webhook when it has one."""

import hashlib
import hmac
import json
import logging
import time

import httpx

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.domain.webhook import Webhook
from pinecall.log.logs import Logs
from pinecall.process.connections import Connections
from pinecall.tenancy import webhooks
from pinecall.wire.events import CreditsExhausted, MonitorFired, SpendUnusual
from pinecall.wire.frames import WireModel

# Once, and once again on a connection error or a 5xx: an alert is said once a day, so a URL
# that is down for a minute is told twice and then the log holds it alone.
ATTEMPTS = 2
# A post waits this long for a 2xx; the seal of a call is behind it.
WAITS_S = 5.0
EVENT_HEADER = "x-pinecall-event"
SIGNATURE_HEADER = "x-pinecall-signature"
A_TEST = "webhook.test"
A_SERVER_ERROR = 500
# The alerts, each under the type the agent's log writes it as.
KIND_OF: dict[type[WireModel], str] = {
    MonitorFired: "monitor.fired",
    SpendUnusual: "spend.unusual",
    CreditsExhausted: "credits.exhausted",
}

logger = logging.getLogger(__name__)


async def raised(
    connections: Connections, logs: Logs, scope: Scope, agent: str, alert: WireModel
) -> None:
    """Write the alert on the agent's log; post it to the org's webhook when it set one."""
    kind, data = KIND_OF[type(alert)], alert.written()
    await logs.agent(agent).append(kind, data)
    webhook = await webhooks.webhook_of(connections.pool, connections.vault, scope.org)
    if webhook is None:
        return
    error = await posted(connections.http, webhook, payload(scope, agent, kind, data))
    if error is not None:
        logger.warning(
            "org %s: %s was not delivered to %s: %s", scope.org, kind, webhook.url, error
        )


async def posted(http: httpx.AsyncClient, webhook: Webhook, sent: JsonObject) -> str | None:
    """Post the payload, again on a connection error or a 5xx; None on a 2xx, else why not."""
    body = json.dumps(sent, separators=(",", ":")).encode()
    headers = {"content-type": "application/json", EVENT_HEADER: str(sent["type"])}
    if webhook.secret is not None:
        digest = hmac.new(webhook.secret.encode(), body, hashlib.sha256).hexdigest()
        headers[SIGNATURE_HEADER] = f"sha256={digest}"
    error = "never posted"
    for _ in range(ATTEMPTS):
        try:
            answer = await http.post(webhook.url, content=body, headers=headers, timeout=WAITS_S)
        except httpx.HTTPError as unreachable:
            error = f"{type(unreachable).__name__}: {unreachable}"
            continue
        if answer.is_success:
            return None
        error = f"HTTP {answer.status_code}"
        if answer.status_code < A_SERVER_ERROR:
            return error
    return error


def payload(scope: Scope, agent: str | None, kind: str, data: JsonObject) -> JsonObject:
    """What a post carries: the kind, whose world it is, the agent, when, and the alert itself."""
    return {
        "type": kind,
        "org": scope.org,
        "env": scope.env,
        "agent": agent,
        "at": time.time(),
        "data": data,
    }
