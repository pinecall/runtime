"""Tests for the livekit Agent the session runs: what its ears let through, what a run keeps."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from livekit.agents import (
    AgentStateChangedEvent,
    stt,
)
from livekit.agents.voice import Agent, ModelSettings

from pinecall.domain.agent import AgentConfig
from pinecall.log.store import Store
from pinecall.session import hold as hold_module
from pinecall.session import text
from pinecall.session._agent import LLM_TIMEOUT
from pinecall.session.hold import HoldMusic
from tests.conftest import postgres
from tests.fakes.livekit import Player
from tests.session.conftest import (
    Box,
    a_session,
    kinds,
    model_of,
)
from tests.session.test_session import (
    NOBODY,
    no_audio,
    speech_event,
)


@postgres
async def test_a_backchannel_over_the_agents_voice_never_reaches_the_model(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    heard = [speech_event("sí, claro"), speech_event("sí, pero el martes no puedo")]

    async def recognised(
        _agent: Agent, _audio: object, _settings: ModelSettings
    ) -> AsyncIterator[stt.SpeechEvent]:
        for event in heard:
            yield event

    monkeypatch.setattr(Agent.default, "stt_node", recognised)
    session = a_session(box, NOBODY)
    await session.start()
    session.live.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    over_the_agent = [event async for event in session.agent.stt_node(no_audio(), ModelSettings())]
    session.live.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="speaking", new_state="listening")
    )
    after_it = [event async for event in session.agent.stt_node(no_audio(), ModelSettings())]
    await text.end(session, "caller_hung_up", "caller")
    assert [event.alternatives[0].text for event in over_the_agent] == [
        "sí, pero el martes no puedo"
    ]
    assert len(after_it) == 2


@postgres
async def test_a_call_an_eval_run_opened_keeps_every_request_the_model_was_sent(box: Box) -> None:
    session = a_session(box, NOBODY, ["hola"], ["adiós"], run="run_1")
    await session.start()
    await text.hears(session, "buenas")
    await text.hears(session, "gracias")
    await text.end(session, "caller_hung_up", "caller")
    requests = session.call.requests
    assert len(requests) == 2
    assert {"system", "tools", "messages"} <= set(requests[0])
    assert "buenas" in str(requests[0]["messages"])


@postgres
async def test_a_real_callers_call_keeps_no_request(box: Box) -> None:
    session = a_session(box, NOBODY, ["hola"])
    await session.start()
    await text.hears(session, "buenas")
    await text.end(session, "caller_hung_up", "caller")
    assert session.call.requests == []


# ── a slow model ──


@postgres
async def test_a_model_past_the_agents_deadline_is_cut_and_the_turn_ends_unanswered(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", llm_timeout_s=0.05), ["tarde"])
    model_of(session).thinks_s = 5.0
    await session.start()
    sentence = await text.hears(session, "hola")
    await text.end(session, "caller_hung_up", "caller")
    assert sentence == ""
    (cut,) = [entry.data for entry in await store.whole(call) if entry.type == "error"]
    assert (cut["code"], cut["recoverable"]) == (LLM_TIMEOUT, True)
    assert "0.05s" in str(cut["message"])
    assert len(model_of(session).requests) == 1


@postgres
async def test_an_agent_with_no_deadline_waits_for_its_model_as_long_as_it_takes(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["por fin"])
    model_of(session).thinks_s = 0.2
    await session.start()
    sentence = await text.hears(session, "hola")
    await text.end(session, "caller_hung_up", "caller")
    assert sentence == "por fin"
    assert "error" not in await kinds(store, call)


@postgres
async def test_the_wait_for_a_slow_model_plays_the_melody_and_its_first_word_stops_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 0.01)
    session = a_session(box, NOBODY, ["listo"])
    model_of(session).thinks_s = 0.2
    melody = HoldMusic(Path("hold.ogg"))
    player = Player()
    melody.player = player
    await session.start(hold=melody)
    await text.hears(session, "hola")
    await text.end(session, "caller_hung_up", "caller")
    assert player.played == [("hold.ogg", True)]
    assert all(handle.done() for handle in player.handles)
    assert melody.running == 0


@postgres
async def test_a_model_that_answers_inside_the_grace_plays_nothing(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 5.0)
    session = a_session(box, NOBODY, ["ya"])
    melody = HoldMusic(Path("hold.ogg"))
    player = Player()
    melody.player = player
    await session.start(hold=melody)
    await text.hears(session, "hola")
    await text.end(session, "caller_hung_up", "caller")
    assert player.played == []
    assert (melody.running, melody.pending) == (0, None)
