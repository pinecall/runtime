"""The bodies of the fleet doors: a worker's heartbeat and what the gateway answers it."""

from pydantic import ConfigDict

from pinecall.wire.frames import WireModel


class HeartbeatRequest(WireModel):
    """A worker's report: its fleet, its name, what it holds and how loaded it is."""

    fleet: str
    worker: str
    active: int
    # Measured slots; null when the worker is gated on its machine's CPU.
    max_jobs: int | None
    load: float
    draining: bool


class HeartbeatResponse(WireModel):
    """The answer to a heartbeat: whether this worker is cordoned, and its fleet full."""

    cordoned: bool
    full: bool


class FleetTotals(WireModel):
    """One fleet over the workers heard from lately."""

    fleet: str
    workers: int
    active: int
    seats: int
    free: int
    accepting: int
    full: bool


class WorkerStatus(WireModel):
    """One worker as its last heartbeat left it, and whether the operator cordoned it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fleet: str
    worker: str
    active: int
    max_jobs: int | None
    load: float
    draining: bool
    cordoned: bool
    seen_at: float
