"""A call's traces, exported over OTLP when the box names an endpoint."""

from livekit.agents.telemetry import set_tracer_provider
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pinecall.domain.errors import DeclarationRefused
from pinecall.process.settings import Settings

SERVICE = "pinecall-worker"


FLEET_ATTRIBUTE = "pinecall.fleet"


NOT_A_HEADER = "an OTLP header is name=value, comma separated; {said!r} is not"


# livekit's tracer does nothing until handed a provider; once per job process, since the
# provider owns a thread.
def traced_to(settings: Settings) -> bool:
    """Send the process's spans where PINECALL_OTLP_ENDPOINT says; False when it says nowhere."""
    if settings.otlp_endpoint is None:
        return False
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE}))
    exporter = OTLPSpanExporter(
        endpoint=settings.otlp_endpoint, headers=otlp_headers(settings.otlp_headers)
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    set_tracer_provider(
        provider, metadata={FLEET_ATTRIBUTE: settings.fleet}, allow_pii=settings.otlp_pii
    )
    return True


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
