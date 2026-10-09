"""The org's monitors: a number of the series watched over a window, listed, set and dropped."""

from fastapi import APIRouter

from pinecall.domain.errors import NotFound
from pinecall.domain.monitor import Monitor
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep
from pinecall.tenancy import monitors
from pinecall.wire.rest.monitors import MonitorList, MonitorRequest, MonitorRow

router = APIRouter(tags=["monitors"])

NO_SUCH_MONITOR = "no monitor {id} in this world: GET /v1/monitors lists them"


@router.get("/v1/monitors")
async def list_monitors(_key: EvalsKey, scope: ScopeDep, gateway: GatewayDep) -> MonitorList:
    """The world's monitors, oldest first, each with the last day it fired."""
    found = await monitors.monitors_of(gateway.connections.pool, scope)
    return MonitorList(monitors=[_row(monitor) for monitor in found])


@router.post("/v1/monitors", status_code=201)
async def add_monitor(
    body: MonitorRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> MonitorRow:
    """Watch a number: it fires, once a day, when it crosses the line over the window."""
    wanted = Monitor(
        id="",
        name=body.name,
        metric=body.metric,
        above=body.above,
        threshold=body.threshold,
        window_days=body.window_days,
        agent=body.agent,
    )
    bearer = key.bearer.key
    author = bearer.subject or bearer.key_id
    kept = await monitors.put_monitor(gateway.connections.pool, scope, wanted, author)
    return _row(kept)


@router.delete("/v1/monitors/{monitor_id}", status_code=204)
async def drop_monitor(
    monitor_id: str, _key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> None:
    """Stop watching."""
    if not await monitors.drop_monitor(gateway.connections.pool, scope, monitor_id):
        raise NotFound(NO_SUCH_MONITOR.format(id=monitor_id))


def _row(monitor: Monitor) -> MonitorRow:
    return MonitorRow(
        id=monitor.id,
        name=monitor.name,
        metric=monitor.metric,
        above=monitor.above,
        threshold=monitor.threshold,
        window_days=monitor.window_days,
        agent=monitor.agent,
        created_by=monitor.created_by,
        fired_on=None if monitor.fired_on is None else monitor.fired_on.isoformat(),
        fired_value=monitor.fired_value,
    )
