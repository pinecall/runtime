"""Tests for the webhook doors: set, read, dropped, scoped, and a test post waited for."""

import json

from tests.conftest import Knocking, issued, postgres
from tests.fakes.webhooks import Receiver

WEBHOOK = "/v1/webhook"


@postgres
async def test_the_org_sets_its_webhook_reads_it_back_without_the_secret_and_drops_it(
    knocking: Knocking, receiver: Receiver
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        nowhere = await org.get(WEBHOOK)
        untested = await org.post(f"{WEBHOOK}/test")
        kept = await org.put(WEBHOOK, json={"url": receiver.url, "secret": "shh"})
        read = await org.get(WEBHOOK)
        bad = await org.put(WEBHOOK, json={"url": "hooks.example.test"})
        tested = await org.post(f"{WEBHOOK}/test")
        dropped = await org.delete(WEBHOOK)
        again = await org.delete(WEBHOOK)
    assert (nowhere.status_code, nowhere.json()) == (200, None)
    assert untested.status_code == 409
    assert kept.status_code == 204
    assert read.json() == {"url": receiver.url, "signed": True}
    assert "shh" not in read.text
    assert bad.status_code == 400
    assert tested.json() == {"sent": True, "error": None}
    [post] = receiver.heard
    assert post.headers["x-pinecall-event"] == "webhook.test"
    assert post.headers["x-pinecall-signature"].startswith("sha256=")
    sent = json.loads(post.content)
    assert (sent["type"], sent["org"], sent["env"], sent["agent"]) == (
        "webhook.test",
        knocking.org.id,
        "sandbox",
        None,
    )
    assert (dropped.status_code, again.status_code) == (204, 404)


@postgres
async def test_a_key_without_the_providers_scope_sets_no_webhook(knocking: Knocking) -> None:
    reads = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reads) as reader:
        refused = await reader.put(WEBHOOK, json={"url": "https://hooks.example.test"})
    assert refused.status_code == 403
