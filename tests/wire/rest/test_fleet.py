"""Tests for the bodies of the fleet doors."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import JsonObject
from pinecall.wire.rest.fleet import HeartbeatRequest, HeartbeatResponse

A_BEAT: JsonObject = {
    "fleet": "pinecall",
    "worker": "box-1",
    "active": 2,
    "max_jobs": None,
    "load": 0.4,
    "draining": False,
}


def test_a_heartbeat_reads_a_worker_gated_on_its_cpu_and_writes_it_back_whole() -> None:
    beat = HeartbeatRequest.read(A_BEAT, "heartbeat")
    assert beat.max_jobs is None
    assert beat.written() == A_BEAT


def test_a_heartbeat_with_a_key_nobody_declared_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="heartbeat"):
        HeartbeatRequest.read({**A_BEAT, "gpu": 1}, "heartbeat")
    assert HeartbeatResponse(cordoned=False, full=True).written() == {
        "cordoned": False,
        "full": True,
    }
