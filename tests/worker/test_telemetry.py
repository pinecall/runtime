"""A call's spans go where PINECALL_OTLP_ENDPOINT says, with the fleet on each; nowhere unset."""

from __future__ import annotations

import re
from typing import Any

import pytest
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pinecall._settings import load_settings
from pinecall.worker import telemetry
from pinecall.worker.telemetry import FLEET, NOT_A_HEADER, headers_of, traced_to

pytestmark = pytest.mark.unit

AN_ENDPOINT = "http://collector.test:4318/v1/traces"


def test_a_box_naming_no_endpoint_traces_nothing_and_builds_no_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PINECALL_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", _never_built)
    assert traced_to(load_settings()) is False


def test_the_endpoint_and_its_headers_reach_the_exporter_and_the_fleet_reaches_every_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINECALL_OTLP_ENDPOINT", AN_ENDPOINT)
    monkeypatch.setenv("PINECALL_OTLP_HEADERS", "Authorization=Basic abc, X-Org=clinica")
    monkeypatch.setenv("PINECALL_FLEET", "clinica-fleet")
    built: dict[str, Any] = {}
    memory = InMemorySpanExporter()

    def an_exporter(**said: Any) -> InMemorySpanExporter:
        built.update(said)
        return memory

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", an_exporter)
    handed: dict[str, Any] = {}

    def a_provider_handed(provider: Any, **said: Any) -> None:
        handed.update(said, provider=provider)

    monkeypatch.setattr(telemetry, "set_tracer_provider", a_provider_handed)

    assert traced_to(load_settings()) is True
    assert built == {
        "endpoint": AN_ENDPOINT,
        "headers": {"Authorization": "Basic abc", "X-Org": "clinica"},
    }
    assert (handed["metadata"], handed["allow_pii"]) == ({FLEET: "clinica-fleet"}, False)
    provider = handed["provider"]
    assert provider.resource.attributes[SERVICE_NAME] == "pinecall-worker"
    provider.get_tracer("a-test").start_span("one turn").end()
    provider.force_flush()
    assert [span.name for span in memory.get_finished_spans()] == ["one turn"]


def test_what_was_said_travels_only_when_the_box_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINECALL_OTLP_ENDPOINT", AN_ENDPOINT)
    monkeypatch.setenv("PINECALL_OTLP_PII", "true")
    handed: dict[str, Any] = {}

    def a_provider_handed(provider: Any, **said: Any) -> None:  # noqa: ARG001
        handed.update(said)

    monkeypatch.setattr(telemetry, "set_tracer_provider", a_provider_handed)
    assert traced_to(load_settings()) is True
    assert handed["allow_pii"] is True


def test_headers_are_spelled_as_the_otel_variable_spells_them() -> None:
    assert headers_of(None) == {}
    assert headers_of("") == {}
    assert headers_of("a=1") == {"a": "1"}
    assert headers_of(" a = 1 ,b=x=y,") == {"a": "1", "b": "x=y"}


@pytest.mark.parametrize("said", ["Authorization", "=x", "a=1,nope"])
def test_a_header_with_no_name_or_no_equals_is_refused_naming_the_variable(said: str) -> None:
    broken = next(pair for pair in said.split(",") if "=" not in pair or pair.startswith("="))
    with pytest.raises(
        ValueError,
        match=re.escape(NOT_A_HEADER.format(variable="PINECALL_OTLP_HEADERS", said=broken)),
    ):
        headers_of(said)


def _never_built(**said: Any) -> Any:
    raise AssertionError(f"an exporter was built for nowhere: {said}")
