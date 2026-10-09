"""/v1/telemetry: where the org sends its calls' traces."""

from pydantic import Field

from pinecall.wire.frames import WireModel


class TelemetryRequest(WireModel):
    """PUT /v1/telemetry: the collector, its headers (a credential, never read back), and PII."""

    endpoint: str
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    pii: bool = False


class TelemetryResponse(WireModel):
    """GET /v1/telemetry: the collector and which headers are set; null when traces go nowhere."""

    endpoint: str
    header_names: list[str]
    pii: bool
