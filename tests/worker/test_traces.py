"""Tests for a call's traces over OTLP: the box's collector, and each org's."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pinecall.domain.telemetry import Telemetry
from pinecall.process.settings import Settings
from pinecall.worker._traces import OrgSpans, traced_to

A_COLLECTOR = Telemetry("https://otel.example.test/v1/traces", {"x-api-key": "made-up"})


def test_a_box_naming_no_endpoint_still_routes_each_call_to_its_org() -> None:
    assert isinstance(traced_to(Settings.model_validate({})), OrgSpans)


def test_a_jobs_spans_carry_the_calls_attributes_and_an_org_without_a_collector_gets_none() -> None:
    kept = InMemorySpanExporter()
    provider = TracerProvider()
    spans = OrgSpans()
    provider.add_span_processor(spans)
    provider.add_span_processor(SimpleSpanProcessor(kept))
    spans.route_to(None, {"pinecall.org": "org_1", "pinecall.call": "CA_1"})
    with provider.get_tracer("test").start_as_current_span("llm_node"):
        pass
    spans.done()
    [span] = kept.get_finished_spans()
    assert span.attributes is not None
    assert (span.attributes["pinecall.org"], span.attributes["pinecall.call"]) == ("org_1", "CA_1")
    assert spans.current is None


def test_an_orgs_collector_is_routed_to_for_the_job_and_let_go_at_its_end() -> None:
    spans = OrgSpans()
    spans.route_to(A_COLLECTOR, {"pinecall.org": "org_1"})
    assert spans.current is not None
    assert spans.redacting
    spans.route_to(Telemetry(A_COLLECTOR.endpoint, pii=True), {})
    assert not spans.redacting
    spans.done()
    assert (spans.current, spans.attributes) == (None, {})
