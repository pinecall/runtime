"""Where a call's spans go: livekit's tracer, given an OTLP exporter when the box names one."""

from __future__ import annotations

import logging

from livekit.agents.telemetry import set_tracer_provider
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pinecall._settings import Settings, variable_of

logger = logging.getLogger(__name__)

SERVICE = "pinecall-worker"

# The one attribute of ours on every span: which instance's worker answered.
FLEET = "pinecall.fleet"

NOT_A_HEADER = "{variable}: each header is name=value, comma separated; {said!r} is not"


# livekit spans a session, each turn, each model and TTS call and each tool
# (agents/telemetry/trace_types.py) on a tracer that is a no-op until it is given a provider.
# The exporter is OTLP over HTTP because every backend speaks it — Langfuse, Grafana, Honeycomb,
# a collector on the box — and a header is how each takes its credential. Once per job process,
# from the prewarm hook: the provider holds a thread and a queue of its own.
def traced_to(settings: Settings) -> bool:
    """livekit's tracer given a provider exporting to PINECALL_OTLP_ENDPOINT; False when unset."""
    if settings.otlp_endpoint is None:
        return False
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE}))
    exporter = OTLPSpanExporter(
        endpoint=settings.otlp_endpoint, headers=headers_of(settings.otlp_headers)
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    set_tracer_provider(provider, metadata={FLEET: settings.fleet}, allow_pii=settings.otlp_pii)
    logger.info(
        "traces go to %s, what was said %s",
        settings.otlp_endpoint,
        "kept" if settings.otlp_pii else "stripped",
    )
    return True


def headers_of(said: str | None) -> dict[str, str]:
    """`name=value,name=value`, as OTEL_EXPORTER_OTLP_HEADERS spells it; empty when unset."""
    headers: dict[str, str] = {}
    for pair in (said or "").split(","):
        if not pair.strip():
            continue
        name, equals, value = pair.partition("=")
        if not equals or not name.strip():
            raise ValueError(NOT_A_HEADER.format(variable=variable_of("otlp_headers"), said=pair))
        headers[name.strip()] = value.strip()
    return headers
