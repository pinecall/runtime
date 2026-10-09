"""Tests for an alert: on the agent's log, posted signed to the org's webhook, tried twice."""

import hashlib
import hmac
import json

from pinecall.domain.scope import Scope
from pinecall.domain.webhook import Webhook
from pinecall.gateway._alerts import raised
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy import orgs, webhooks
from pinecall.wire.events import SpendUnusual
from tests.conftest import postgres
from tests.fakes.webhooks import Receiver

AGENT = "front-desk"
UNUSUAL = SpendUnusual(org="", day="2026-10-09", today_usd=9.0, usual_usd=2.0, multiple=4.5)


async def _a_world(wired: Gateway) -> Scope:
    org = await orgs.create(wired.connections.pool, "clinica-norte", "Clinica Norte")
    return Scope(org.id, "production")


@postgres
async def test_an_alert_is_written_on_the_log_and_posted_signed_with_its_world(
    wired: Gateway, receiver: Receiver
) -> None:
    scope = await _a_world(wired)
    connections = wired.connections
    await webhooks.put_webhook(
        connections.pool, connections.vault, scope.org, Webhook(receiver.url, "shh")
    )
    await raised(connections, wired.logs, scope, AGENT, UNUSUAL)
    [entry] = await wired.logs.store.whole(f"@{AGENT}")
    assert (entry.type, entry.data["multiple"]) == ("spend.unusual", 4.5)
    [post] = receiver.heard
    sent = json.loads(post.content)
    assert (sent["type"], sent["org"], sent["env"], sent["agent"]) == (
        "spend.unusual",
        scope.org,
        "production",
        AGENT,
    )
    assert sent["data"] == UNUSUAL.written()
    digest = hmac.new(b"shh", post.content, hashlib.sha256).hexdigest()
    assert post.headers["x-pinecall-signature"] == f"sha256={digest}"
    assert post.headers["x-pinecall-event"] == "spend.unusual"


@postgres
async def test_a_url_that_fails_is_tried_once_more_and_a_refusal_is_not(
    wired: Gateway, receiver: Receiver
) -> None:
    scope = await _a_world(wired)
    connections = wired.connections
    await webhooks.put_webhook(
        connections.pool, connections.vault, scope.org, Webhook(receiver.url)
    )
    receiver.answers = [503, 200]
    await raised(connections, wired.logs, scope, AGENT, UNUSUAL)
    assert len(receiver.heard) == 2
    assert "x-pinecall-signature" not in receiver.heard[0].headers
    receiver.answers = [404]
    await raised(connections, wired.logs, scope, AGENT, UNUSUAL)
    assert len(receiver.heard) == 3


@postgres
async def test_an_org_with_no_webhook_keeps_the_alert_on_the_log_alone(
    wired: Gateway, receiver: Receiver
) -> None:
    scope = await _a_world(wired)
    await raised(wired.connections, wired.logs, scope, AGENT, UNUSUAL)
    assert len(await wired.logs.store.whole(f"@{AGENT}")) == 1
    assert receiver.heard == []
