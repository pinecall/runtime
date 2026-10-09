"""Tests for a call's traces over OTLP: the box's collector, and each org's."""

import dataclasses
from datetime import date

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pinecall.domain.call import CallContext, Contact, Route
from pinecall.domain.telemetry import Telemetry
from pinecall.process.settings import Settings
from pinecall.worker._traces import OrgSpans, attributes_of, trace_id_of, traced_to

A_COLLECTOR = Telemetry("https://otel.example.test/v1/traces", {"x-api-key": "made-up"})


def test_a_box_naming_no_endpoint_still_routes_each_call_to_its_org() -> None:
    assert isinstance(traced_to(Settings.model_validate({})), OrgSpans)


def test_a_jobs_spans_carry_the_calls_attributes_and_an_org_without_a_collector_gets_none() -> None:
    kept = InMemorySpanExporter()
    provider = TracerProvider()
    spans = OrgSpans()
    provider.add_span_processor(spans)
    provider.add_span_processor(SimpleSpanProcessor(kept))
    spans.route_to(None, {"pinecall.org": "org_1", "pinecall.call": "CA_1"}, "CA_1")
    with provider.get_tracer("test").start_as_current_span("llm_node"):
        pass
    spans.done()
    [span] = kept.get_finished_spans()
    assert span.attributes is not None
    assert (span.attributes["pinecall.org"], span.attributes["pinecall.call"]) == ("org_1", "CA_1")
    assert spans.current is None


def test_a_calls_spans_share_a_trace_id_a_reader_can_compute_from_the_call_id() -> None:
    kept = InMemorySpanExporter()
    spans = OrgSpans()
    provider = TracerProvider(id_generator=spans.ids)
    provider.add_span_processor(spans)
    provider.add_span_processor(SimpleSpanProcessor(kept))
    spans.route_to(None, {}, "call_7216eb82996a429f88ddd4173cff5c3f")
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("agent_session"), tracer.start_as_current_span("llm_node"):
        pass
    spans.done()
    ids = {trace_of(span) for span in kept.get_finished_spans()}
    assert ids == {int("7216eb82996a429f88ddd4173cff5c3f", 16)}
    assert trace_id_of("call-_+34607827824_GCrodJQ2ozT9") != trace_id_of("call-_+34600000000_x")
    with tracer.start_as_current_span("between_jobs"):
        pass
    assert trace_of(kept.get_finished_spans()[-1]) not in ids


def test_an_orgs_collector_is_routed_to_for_the_job_and_let_go_at_its_end() -> None:
    spans = OrgSpans()
    spans.route_to(A_COLLECTOR, {"pinecall.org": "org_1"}, "CA_1")
    assert spans.current is not None
    assert spans.redacting
    spans.route_to(Telemetry(A_COLLECTOR.endpoint, pii=True), {}, "CA_2")
    assert not spans.redacting
    spans.done()
    assert (spans.current, spans.attributes) == (None, {})


def trace_of(span: ReadableSpan) -> int:
    context = span.get_span_context()
    assert context is not None
    return context.trace_id


def test_a_calls_spans_say_its_world_session_and_contact_as_any_backend_reads_them() -> None:
    route = Route(
        org="org_1", agent="front-desk", channel="phone", number="+59829001199", env="sandbox"
    )
    known = CallContext(
        call="CA_1",
        channel="phone",
        direction="inbound",
        caller="+59899123456",
        route=route,
        contact=Contact(id="ct_ana", phone="+59899123456"),
        today=date(2026, 10, 9),
    )
    carried = attributes_of(known)
    assert (carried["deployment.environment.name"], carried["session.id"], carried["user.id"]) == (
        "sandbox",
        "CA_1",
        "ct_ana",
    )
    assert "+59899123456" not in carried.values(), "the number is never an attribute"
    anonymous = attributes_of(dataclasses.replace(known, contact=None))
    assert "user.id" not in anonymous
