"""A call's traces: livekit's spans over OTLP to the box's collector, and to the org's, per call."""

from typing import override

from livekit.agents.telemetry import set_tracer_provider
from livekit.agents.telemetry.pii import redact
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.telemetry import Telemetry
from pinecall.process.settings import Settings

SERVICE = "pinecall-worker"


FLEET_ATTRIBUTE = "pinecall.fleet"


NOT_A_HEADER = "an OTLP header is name=value, comma separated; {said!r} is not"


class OrgSpans(SpanProcessor):
    """The org's collector for the job running: routed to at the call's start, let go at its end."""

    def __init__(self) -> None:
        """Nothing routed yet."""
        self.current: BatchSpanProcessor | None = None
        self.redacting = False
        self.attributes: dict[str, str] = {}

    def route_to(self, telemetry: Telemetry | None, attributes: dict[str, str]) -> None:
        """Send this job's spans to the org's collector, each carrying the call's attributes."""
        self.done()
        self.attributes = dict(attributes)
        if telemetry is None:
            return
        exporter = OTLPSpanExporter(endpoint=telemetry.endpoint, headers=dict(telemetry.headers))
        self.current = BatchSpanProcessor(exporter)
        self.redacting = not telemetry.pii

    def done(self) -> None:
        """Flush what the job left and stop sending to its org."""
        if self.current is not None:
            self.current.shutdown()
            self.current = None
        self.attributes = {}

    @override
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        """Every span of the job names the call, whoever reads it."""
        for name, value in self.attributes.items():
            span.set_attribute(name, value)
        if self.current is not None:
            self.current.on_start(span, parent_context)

    @override
    def on_end(self, span: ReadableSpan) -> None:
        """The org's collector gets the span, stripped of PII unless the org allowed it."""
        if self.current is not None:
            self.current.on_end(redact(span) if self.redacting else span)

    @override
    def shutdown(self) -> None:
        """The process is closing."""
        self.done()

    @override
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Wait for the org's exporter to send what it holds."""
        return self.current.force_flush(timeout_millis) if self.current is not None else True


# livekit's tracer does nothing until handed a provider; once per job process, since the
# provider owns a thread. The box's exporter is there when the box names an endpoint; the
# org's processor is always there, routed per job.
def traced_to(settings: Settings) -> OrgSpans:
    """The process's spans go where PINECALL_OTLP_ENDPOINT says, and to each call's org."""
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE}))
    if settings.otlp_endpoint is not None:
        exporter = OTLPSpanExporter(
            endpoint=settings.otlp_endpoint, headers=otlp_headers(settings.otlp_headers)
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    spans = OrgSpans()
    provider.add_span_processor(spans)
    set_tracer_provider(
        provider, metadata={FLEET_ATTRIBUTE: settings.fleet}, allow_pii=settings.otlp_pii
    )
    return spans


def otlp_headers(text: str | None) -> dict[str, str]:
    """`name=value,name=value`, as OTEL_EXPORTER_OTLP_HEADERS spells them."""
    headers: dict[str, str] = {}
    for pair in (text or "").split(","):
        if not pair.strip():
            continue
        name, equals, value = pair.partition("=")
        if not equals or not name.strip():
            raise DeclarationRefused(NOT_A_HEADER.format(said=pair))
        headers[name.strip()] = value.strip()
    return headers
