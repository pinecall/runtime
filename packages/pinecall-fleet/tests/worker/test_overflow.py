"""The overflow agent's gate: closed until the hub says the fleet is full, and open only then."""

from __future__ import annotations

from typing import cast

import pytest
from livekit.agents import AgentServer

from pinecall.worker.overflow import CLOSED, OPEN, OverflowGate, Watching
from tests.worker.fakes import a_gateway

pytestmark = pytest.mark.unit

STANDING = "/v1/fleet/standing"
A_SERVER = cast(AgentServer, object())


def test_the_gate_reports_full_until_told_otherwise() -> None:
    """livekit hands a job to no worker at 1.0, and picks weighted by 1 − load under 0.7."""
    gate = OverflowGate()
    assert gate(A_SERVER) == CLOSED == 1.0
    gate.fleet_is_full = True
    assert gate(A_SERVER) == OPEN == 0.0


async def test_watching_opens_the_gate_when_the_hub_says_full_and_closes_it_after() -> None:
    gate = OverflowGate()
    full = {"workers": 2, "active": 8, "seats": 8, "free": 0, "accepting": 0, "full": True}
    watching = Watching(gate, a_gateway({STANDING: full}))
    await watching._once()  # pyright: ignore[reportPrivateUsage]
    assert gate.fleet_is_full is True
    not_full = {**full, "accepting": 1, "full": False}
    watching = Watching(gate, a_gateway({STANDING: not_full}))
    await watching._once()  # pyright: ignore[reportPrivateUsage]
    assert gate.fleet_is_full is False


async def test_a_hub_that_does_not_answer_leaves_the_gate_as_it_was() -> None:
    gate = OverflowGate()
    gate.fleet_is_full = True
    await Watching(gate, a_gateway({}))._once()  # pyright: ignore[reportPrivateUsage]
    assert gate.fleet_is_full is True
