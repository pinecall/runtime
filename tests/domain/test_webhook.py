"""Tests for where an org's alerts go."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.webhook import Webhook

SHH = "shh"


def test_a_webhook_is_an_http_url_with_or_without_a_secret() -> None:
    assert Webhook("https://hooks.example.test/alerts").secret is None
    assert Webhook("http://localhost:8080/", SHH).secret == SHH
    with pytest.raises(DeclarationRefused, match="http\\(s\\) URL"):
        Webhook("hooks.example.test/alerts")
