"""Tests for the wait between a simulated caller's lines: the agent's whole answer, or none."""

import asyncio

from pinecall.evals.simulated import answered
from pinecall.log.logs import Subscription
from pinecall.log.readers import EVERYTHING
from tests.evals.conftest import entry


def a_reader() -> Subscription:
    return Subscription(EVERYTHING, lambda _: None)


async def test_the_caller_waits_through_the_agents_thinking_for_its_turn() -> None:
    heard = a_reader()

    async def reply() -> None:
        heard.offer(entry(1, "turn.user", {"text": "quiero un turno"}))
        await asyncio.sleep(0.3)
        heard.offer(entry(2, "turn.agent", {"text": "el martes a las diez"}))

    answering = asyncio.create_task(reply())
    assert await answered(heard, within_s=2.0) is True
    await answering


async def test_a_call_ended_under_the_caller_ends_the_wait_too() -> None:
    heard = a_reader()
    heard.offer(entry(1, "call.ended", {"reason": "agent_hung_up"}))
    assert await answered(heard, within_s=2.0) is True


async def test_an_agent_that_never_answers_is_told_apart_from_one_that_did() -> None:
    heard = a_reader()
    heard.offer(entry(1, "turn.user", {"text": "hola"}))
    assert await answered(heard, within_s=0.2) is False
