"""`GET /metrics`: what the gateway counted and holds, as Prometheus text, on loopback alone."""

import ipaddress
import time

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from pinecall.channels import offers
from pinecall.domain.errors import NotAllowed
from pinecall.fleet.roster import heard_lately, worker_state
from pinecall.gateway._deps import GatewayDep, client_of
from pinecall.gateway._gateway import Gateway
from pinecall.process.metrics import family, histogram

router = APIRouter()


# Caddy sets it on every request it passes on: a request that carries it came from off the box.
FORWARDED = "x-forwarded-for"

NOT_HERE = (
    "/metrics answers the addresses PINECALL_METRICS_FROM names (the box's loopback unless said), "
    "never a forwarded request: curl http://127.0.0.1:8080/metrics on the box"
)

TEXT_FORMAT = "text/plain; version=0.0.4; charset=utf-8"

# What each standby is behind, as the primary sees it: empty on a box with no replica, and on a
# standby itself. A lag never replayed yet (a replica caught up and idle) reads as zero.
REPLICAS = """
select application_name as replica, extract(epoch from coalesce(replay_lag, interval '0')) as lag
from pg_stat_replication
"""


# No key: the address is the fence, and a scraper on the box or in the cluster holds none. A
# request through the load balancer always carries X-Forwarded-For, and is refused whatever it says.
@router.get("/metrics", include_in_schema=False)
async def read_metrics(request: Request, gateway: GatewayDep) -> PlainTextResponse:
    """What the gateway counted since it started and what it holds now, in Prometheus's text."""
    allowed = gateway.connections.settings.metrics_from
    if not from_any(client_of(request), allowed) or FORWARDED in request.headers:
        raise NotAllowed(NOT_HERE)
    now = time.time()
    replicas = await _replicas(gateway)
    waiting = await _waiting(gateway, now)
    return PlainTextResponse(_measures_of(gateway, now, replicas, waiting), media_type=TEXT_FORMAT)


def from_any(client: str, networks: str) -> bool:
    """Whether the address is one of the comma-separated addresses or networks."""
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    named = (part.strip() for part in networks.split(",") if part.strip())
    return any(address in ipaddress.ip_network(network, strict=False) for network in named)


# A database that cannot say leaves the family empty: the scrape still answers.
async def _replicas(gateway: Gateway) -> list[tuple[str, float]]:
    try:
        async with gateway.connections.pool.connection() as connection:
            rows = await (await connection.execute(REPLICAS)).fetchall()
    except psycopg.Error:
        return []
    return [(str(row["replica"]), float(row["lag"])) for row in rows]


# The rooms with a caller no worker opened yet, per fleet: a call LiveKit or a worker dropped.
async def _waiting(gateway: Gateway, now: float) -> dict[str, int]:
    try:
        return await offers.waiting(gateway.connections.pool, now - offers.WAITING_COUNTED_S)
    except psycopg.Error:
        return {}


def _measures_of(
    gateway: Gateway, now: float, replicas: list[tuple[str, float]], waiting: dict[str, int]
) -> str:
    """Every family this gateway exposes, in the text format."""
    counted = gateway.counters
    pool = gateway.connections.pool.get_stats()
    live = gateway.live
    fleets = sorted({seat.fleet for seat in gateway.roster.seats.values()})
    totals = [gateway.roster.totals(fleet, now) for fleet in fleets]
    workers = {fleet: gateway.roster.of(fleet, now) for fleet in fleets}
    errors = sorted(counted.errors.items())
    return "".join(
        (
            histogram(
                "pinecall_append_seconds",
                "Time a worker's entry or batch took to be written, measured at the door.",
                counted.append_seconds,
            ),
            family(
                "pinecall_entries_appended_total",
                "Entries workers wrote through the append doors.",
                "counter",
                [({}, counted.appended)],
            ),
            family(
                "pinecall_errors_total",
                "Error entries workers wrote, by code and the vendor whose plugin failed.",
                "counter",
                [({"code": code, "vendor": vendor}, count) for (code, vendor), count in errors],
            ),
            family(
                "pinecall_pool_connections",
                "Connections the pool holds open, lent out now, and at most.",
                "gauge",
                [
                    ({"state": "open"}, pool.get("pool_size", 0)),
                    ({"state": "in_use"}, pool.get("pool_size", 0) - pool.get("pool_available", 0)),
                    ({"state": "max"}, pool.get("pool_max", 0)),
                ],
            ),
            family(
                "pinecall_pool_waiting",
                "Requests waiting for a connection now.",
                "gauge",
                [({}, pool.get("requests_waiting", 0))],
            ),
            family(
                "pinecall_pool_requests_total",
                "Connections asked of the pool.",
                "counter",
                [({}, pool.get("requests_num", 0))],
            ),
            family(
                "pinecall_pool_wait_seconds_total",
                "Time requests spent waiting for a connection.",
                "counter",
                [({}, pool.get("requests_wait_ms", 0) / 1000)],
            ),
            family(
                "pinecall_held",
                "What the gateway holds now: live log readers, app sockets, calls served.",
                "gauge",
                [
                    ({"what": "log_readers"}, gateway.logs.readers),
                    ({"what": "app_sockets"}, len(live.sockets)),
                    ({"what": "calls_live"}, len(live.calls)),
                ],
            ),
            family(
                "pinecall_writer_waiting",
                "Appends queued for the log's writer and not yet in a transaction.",
                "gauge",
                [({}, gateway.logs.store.writer.waiting)],
            ),
            family(
                "pinecall_fleet",
                "Each fleet: workers, seats, calls held, accepting; rooms waiting for a worker.",
                "gauge",
                [
                    ({"fleet": total.fleet, "what": what}, value)
                    for total in totals
                    for what, value in (
                        ("workers", total.workers),
                        ("seats", total.seats),
                        ("busy", total.active),
                        ("accepting", total.accepting),
                        ("waiting", waiting.get(total.fleet, 0)),
                    )
                ],
            ),
            family(
                "pinecall_vendor_failing",
                "Vendors over their error line in the last two minutes, as this gateway saw.",
                "gauge",
                [({"vendor": vendor}, 1) for vendor in sorted(counted.failing(now))],
            ),
            family(
                "pinecall_replication_lag_seconds",
                "How far behind each standby is in replaying the primary's WAL.",
                "gauge",
                [({"replica": replica}, lag) for replica, lag in replicas],
            ),
            family(
                "pinecall_spend_unusual",
                "How many times its usual day an org's calls cost today, while they do.",
                "gauge",
                [({"org": org}, multiple) for org, multiple in sorted(counted.unusual.items())],
            ),
            family(
                "pinecall_worker_state",
                "How the roster counts each worker: accepting, failing, full, draining, cordoned.",
                "gauge",
                [
                    ({"fleet": fleet, "worker": seat.worker, "state": state}, 1)
                    for fleet, seats in workers.items()
                    for seat in seats
                    if (state := worker_state(seat, seats, now)) != "gone"
                ],
            ),
            family(
                "pinecall_worker_first_audio_p95_seconds",
                "Each worker's first audio at the p95 over its last minute, as its heartbeat says.",
                "gauge",
                [
                    ({"fleet": fleet, "worker": seat.worker}, seat.first_audio_p95_s)
                    for fleet, seats in workers.items()
                    for seat in seats
                    if seat.first_audio_p95_s is not None and heard_lately(seat, now)
                ],
            ),
        )
    )
