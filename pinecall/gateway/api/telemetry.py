"""Where the org's calls' traces go: its own OTLP collector, set, read and taken back."""

from fastapi import APIRouter

from pinecall.domain.errors import NotFound
from pinecall.domain.telemetry import Telemetry
from pinecall.gateway._deps import GatewayDep, ProvidersKey
from pinecall.tenancy import telemetry
from pinecall.wire.rest.telemetry import TelemetryRequest, TelemetryResponse

router = APIRouter(tags=["telemetry"])

NOWHERE = "this org sends its traces nowhere: PUT /v1/telemetry names a collector"


@router.get("/v1/telemetry")
async def where_traces_go(key: ProvidersKey, gateway: GatewayDep) -> TelemetryResponse | None:
    """The org's collector and which headers are set, never their values; null when none."""
    connections = gateway.connections
    found = await telemetry.described(connections.pool, connections.vault, key.org)
    if found is None:
        return None
    return TelemetryResponse(
        endpoint=found.endpoint, header_names=list(found.header_names), pii=found.pii
    )


@router.put("/v1/telemetry", status_code=204)
async def send_traces_to(body: TelemetryRequest, key: ProvidersKey, gateway: GatewayDep) -> None:
    """Keep the org's collector; its calls' traces go there from the next one."""
    wanted = Telemetry(endpoint=body.endpoint, headers=body.headers, pii=body.pii)
    connections = gateway.connections
    await telemetry.put_telemetry(connections.pool, connections.vault, key.org, wanted)


@router.delete("/v1/telemetry", status_code=204)
async def stop_sending_traces(key: ProvidersKey, gateway: GatewayDep) -> None:
    """Forget the org's collector; its calls' traces stay on the platform from the next one."""
    if not await telemetry.drop_telemetry(gateway.connections.pool, key.org):
        raise NotFound(NOWHERE)
