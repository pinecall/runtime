"""Where an org's calls' traces go: its own OTLP endpoint, its headers, what a span may carry."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from pinecall.domain.errors import DeclarationRefused

NOT_AN_ENDPOINT = (
    "a telemetry endpoint is an http(s) URL an OTLP exporter can post to, not {said!r}"
)
NOT_A_HEADER_NAME = "a telemetry header has a name"


@dataclass(frozen=True)
class Telemetry:
    """An org's OTLP collector: the endpoint, the headers every export carries, and PII."""

    endpoint: str
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    # Whether a span carries what was said and what a tool got; stripped before export otherwise.
    pii: bool = False

    def __post_init__(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise DeclarationRefused(NOT_AN_ENDPOINT.format(said=self.endpoint))
        if any(not name.strip() for name in self.headers):
            raise DeclarationRefused(NOT_A_HEADER_NAME)
