"""Tests for the telemetry door's shapes."""

from pinecall.wire.rest.telemetry import TelemetryRequest, TelemetryResponse


def test_a_request_names_the_collector_with_no_headers_and_pii_off_unless_said() -> None:
    request = TelemetryRequest.model_validate({"endpoint": "https://otel.example.test"})
    assert (request.headers, request.pii) == ({}, False)


def test_the_answer_names_the_headers_set_and_never_their_values() -> None:
    answered = TelemetryResponse(
        endpoint="https://otel.example.test", header_names=["x-api-key"], pii=False
    )
    assert "made-up" not in answered.model_dump_json()
