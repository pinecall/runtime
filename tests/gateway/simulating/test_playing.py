"""Tests for the simulated calls a process plays: kept by call until they end, stopped with it."""

import asyncio
import logging

import pytest

from pinecall.gateway.simulating.playing import Simulations


async def test_a_call_is_kept_while_it_plays_and_let_go_when_it_ends() -> None:
    simulations = Simulations()
    hung_up = asyncio.Event()

    async def plays() -> None:
        await hung_up.wait()

    simulations.play("call_1", plays())
    await asyncio.sleep(0)
    assert list(simulations.playing) == ["call_1"]
    hung_up.set()
    await asyncio.sleep(0.01)
    assert simulations.playing == {}


async def test_a_call_that_breaks_is_logged_and_let_go(caplog: pytest.LogCaptureFixture) -> None:
    simulations = Simulations()

    async def breaks() -> None:
        raise RuntimeError("the room refused the caller")

    with caplog.at_level(logging.ERROR):
        simulations.play("call_2", breaks())
        await asyncio.sleep(0.01)
    assert simulations.playing == {}
    assert "simulated call call_2 broke" in caplog.text


async def test_every_call_still_playing_stops_with_the_process() -> None:
    simulations = Simulations()
    simulations.play("call_3", asyncio.sleep(60))
    await asyncio.sleep(0)
    await simulations.stopped()
    assert simulations.playing == {}
