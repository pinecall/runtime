"""Tests for the webhook door's shapes."""

from pinecall.wire.rest.webhooks import WebhookRequest, WebhookResponse


def test_a_request_takes_a_url_alone_and_the_answer_says_whether_posts_are_signed() -> None:
    assert WebhookRequest.model_validate({"url": "https://hooks.example.test"}).secret is None
    assert WebhookResponse(url="https://hooks.example.test", signed=True).written() == {
        "url": "https://hooks.example.test",
        "signed": True,
    }
