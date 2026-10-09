"""Tests for where an org's traces go."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.telemetry import Telemetry


def test_a_collector_is_an_http_endpoint_with_its_headers_and_pii_off_unless_said() -> None:
    sent = Telemetry("https://otel.example.test/v1/traces", {"x-api-key": "made-up"})
    assert (sent.headers, sent.pii) == ({"x-api-key": "made-up"}, False)
    assert Telemetry("http://127.0.0.1:4318/v1/traces", pii=True).pii


def test_an_endpoint_that_is_not_a_url_and_a_nameless_header_are_refused() -> None:
    with pytest.raises(DeclarationRefused, match="http"):
        Telemetry("otel.example.test:4317")
    with pytest.raises(DeclarationRefused, match="has a name"):
        Telemetry("https://otel.example.test", {" ": "x"})
