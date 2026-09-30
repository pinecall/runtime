"""`GET /metrics`: what the gateway counted and holds, as Prometheus text, on loopback alone."""

import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from pinecall.domain.errors import NotAllowed
from pinecall.gateway._deps import GatewayDep, client_of
from pinecall.gateway._gateway import Gateway
from pinecall.process.metrics import family, histogram

router = APIRouter()

LOOPBACK = frozenset({"127.0.0.1", "::1"})

# Caddy sets it on every request it passes on: a request that carries it came from off the box.
FORWARDED = "x-forwarded-for"

NOT_HERE = "/metrics answers on the box's loopback alone: curl http://127.0.0.1:8080/metrics there"

TEXT_FORMAT = "text/plain; version=0.0.4; charset=utf-8"


# No key: the address is the fence, and a scraper on the box holds none.
@router.get("/metrics", include_in_schema=False)
async def read_metrics(request: Request, gateway: GatewayDep) -> PlainTextResponse:
    """What the gateway counted since it started and what it holds now, in Prometheus's text."""
    if client_of(request) not in LOOPBACK or FORWARDED in request.headers:
        raise NotAllowed(NOT_HERE)
    return PlainTextResponse(_measures_of(gateway, time.time()), media_type=TEXT_FORMAT)


def _measures_of(gateway: Gateway, now: float) -> str:
    """Every family this gateway exposes, in the text format."""
    counted = gateway.counters
    pool = gateway.connections.pool.get_stats()
    live = gateway.live
    fleets = sorted({seat.fleet for seat in gateway.roster.seats.values()})
    totals = [gateway.roster.totals(fleet, now) for fleet in fleets]
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
                "pinecall_fleet",
                "Each fleet as its workers' heartbeats say: workers, seats, calls held, accepting.",
                "gauge",
                [
                    ({"fleet": total.fleet, "what": what}, value)
                    for total in totals
                    for what, value in (
                        ("workers", total.workers),
                        ("seats", total.seats),
                        ("busy", total.active),
                        ("accepting", total.accepting),
                    )
                ],
            ),
        )
    )
