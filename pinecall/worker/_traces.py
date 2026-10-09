"""A call's traces: livekit's spans over OTLP to the box's collector, and to the org's, per call."""

import hashlib
import re
from typing import override

from livekit.agents.telemetry import set_tracer_provider
from livekit.agents.telemetry.pii import redact
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

from pinecall.domain.call import CallContext
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.telemetry import Telemetry
from pinecall.process.settings import Settings

SERVICE = "pinecall-worker"


FLEET_ATTRIBUTE = "pinecall.fleet"


NOT_A_HEADER = "an OTLP header is name=value, comma separated; {said!r} is not"

# A call id of the gateway's own making: its 32 hex digits are the trace id as they stand.
A_HEX_CALL = re.compile(r"^call_([0-9a-f]{32})$")


class CallTraceIds(RandomIdGenerator):
    """Trace ids that are the call's while a job runs, random between jobs."""

    def __init__(self) -> None:
        """No call yet."""
        super().__init__()
        self.current: int | None = None

    @override
    def generate_trace_id(self) -> int:
        """The call's id, or a random one when no call is running."""
        return self.current if self.current is not None else super().generate_trace_id()


class OrgSpans(SpanProcessor):
    """The org's collector for the job running: routed to at the call's start, let go at its end."""

    def __init__(self) -> None:
        """Nothing routed yet."""
        self.current: BatchSpanProcessor | None = None
        self.redacting = False
        self.attributes: dict[str, str] = {}
        self.ids = CallTraceIds()

    def route_to(self, telemetry: Telemetry | None, attributes: dict[str, str], call: str) -> None:
        """Send this job's spans, under the call's own trace id, to the org's collector."""
        self.done()
        self.attributes = dict(attributes)
        self.ids.current = trace_id_of(call)
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
        self.ids.current = None

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
    spans = OrgSpans()
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: SERVICE}), id_generator=spans.ids
    )
    if settings.otlp_endpoint is not None:
        exporter = OTLPSpanExporter(
            endpoint=settings.otlp_endpoint, headers=otlp_headers(settings.otlp_headers)
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    provider.add_span_processor(spans)
    set_tracer_provider(
        provider, metadata={FLEET_ATTRIBUTE: settings.fleet}, allow_pii=settings.otlp_pii
    )
    return spans


# Ours, and the OpenTelemetry conventions any backend reads — the world as the deployment's
# environment, the call as the session, the contact's id (never the number) as the user — so a
# tool sorts a call into its world, its session and its person without knowing what pinecall.*
# means. Langfuse, Datadog, Honeycomb and Grafana each read these three as such.
def attributes_of(context: CallContext) -> dict[str, str]:
    """What every span of the call carries."""
    env = context.route.env or ""
    contact = context.contact.id if context.contact is not None else None
    return {
        "pinecall.org": context.route.org,
        "pinecall.env": env,
        "pinecall.agent": context.route.agent,
        "pinecall.call": context.call,
        "pinecall.holder": context.holder or "",
        "deployment.environment.name": env,
        "session.id": context.call,
        **({} if contact is None else {"user.id": contact}),
    }


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


def trace_id_of(call: str) -> int:
    """The trace id a call's spans share, derived from the call id so a reader can compute it."""
    found = A_HEX_CALL.match(call)
    digest = found.group(1) if found else hashlib.sha256(call.encode()).hexdigest()[:32]
    return int(digest, 16)
