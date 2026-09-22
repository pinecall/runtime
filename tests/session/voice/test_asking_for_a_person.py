"""call.attention on a spoken call: the caller waits, and either somebody comes or nobody does."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.session.voice.supervising import Supervising
from pinecall_protocol import ProtocolError, verbs
from pinecall_protocol.commands import CallAttention, SupervisorVerb
from pinecall_protocol.defs import Supervisor
from tests.session.voice.test_supervising import Ended, ScriptedAgent
from tests.session.voice.test_the_line_is_held import Held

pytestmark = pytest.mark.unit

ANA = Supervisor(id="sup_ab12cd", name="Ana")
BECAUSE = "the caller wants a refund nobody may promise"


async def test_an_ask_puts_the_caller_on_hold_and_says_what_a_supervisor_is_wanted_for() -> None:
    held = Held()
    await held.applied("call.attention", {"reason": BECAUSE, "wait_s": 30})
    (asked,) = held.recording.of("attention.requested")
    assert (asked.data["reason"], asked.data["wait_s"]) == (BECAUSE, 30)
    assert held.recording.of("call.line")[0].data["held"] is True
    assert held.attending.open


async def test_a_second_ask_while_the_caller_is_already_waiting_is_refused_by_name() -> None:
    held = Held()
    await held.applied("call.attention", {"reason": BECAUSE, "wait_s": 30})
    with pytest.raises(ProtocolError, match="already waiting"):
        await held.applied("call.attention", {"reason": BECAUSE, "wait_s": 30})


async def test_a_supervisor_taking_the_line_answers_the_ask_and_stops_the_melody() -> None:
    held = Held()
    supervising = Supervising(
        held.live,  # pyright: ignore[reportArgumentType]
        ScriptedAgent(),  # pyright: ignore[reportArgumentType]
        held.writing,
        Ended(),
        None,
        held.attending,
    )
    await held.applied("call.attention", {"reason": BECAUSE, "wait_s": 30})
    await supervising.apply(SupervisorVerb(by=ANA, verb=verbs.TakeoverVerb(verb="takeover")))
    await held.writing.flushed()
    (answered,) = held.recording.of("attention.answered")
    assert (answered.data["ok"], answered.data["by"]["id"]) == (True, ANA.id)
    # The melody is gone, and the agent stays exactly as deaf as the takeover left it.
    assert held.recording.of("call.line")[-1].data["held"] is False
    assert (held.live.output.enabled, held.live.input.enabled) == (False, False)
    assert not held.attending.open


async def test_a_wait_nobody_answered_gives_the_agent_the_caller_back() -> None:
    held = Held()
    await held.attending.asked(CallAttention(reason=BECAUSE, wait_s=0.01))
    await asyncio.sleep(0.05)
    await held.writing.flushed()
    (answered,) = held.recording.of("attention.answered")
    assert (answered.data["ok"], answered.data["by"]) == (False, None)
    assert "nobody took the line" in answered.data["error"]
    assert (held.live.output.enabled, held.live.input.enabled) == (True, True)


async def test_a_supervisor_taking_a_line_on_plain_hold_ends_the_hold_for_them() -> None:
    held = Held()
    supervising = Supervising(
        held.live,  # pyright: ignore[reportArgumentType]
        ScriptedAgent(),  # pyright: ignore[reportArgumentType]
        held.writing,
        Ended(),
        None,
        held.attending,
    )
    await held.applied("call.hold")
    await supervising.apply(SupervisorVerb(by=ANA, verb=verbs.TakeoverVerb(verb="takeover")))
    await held.writing.flushed()
    assert held.recording.of("attention.answered") == []
    assert [line.data["held"] for line in held.recording.of("call.line")] == [True, False]
    assert (held.live.output.enabled, held.live.input.enabled) == (False, False)
