"""/v1/monitors: the numbers an org watches, and the line each must not cross."""

from pinecall.domain.monitor import Metric
from pinecall.wire.frames import WireModel


class MonitorRequest(WireModel):
    """POST /v1/monitors: what to watch, over which window, the line, and whose agent."""

    name: str
    metric: Metric
    above: bool
    threshold: float
    window_days: int = 7
    agent: str | None = None


class MonitorRow(WireModel):
    """One monitor: its rule, who set it, and the last day it fired with the value that crossed."""

    id: str
    name: str
    metric: str
    above: bool
    threshold: float
    window_days: int
    agent: str | None
    created_by: str
    fired_on: str | None
    fired_value: float | None


class MonitorList(WireModel):
    """GET /v1/monitors: the world's monitors, oldest first."""

    monitors: list[MonitorRow]
