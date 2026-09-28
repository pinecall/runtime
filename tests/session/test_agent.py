"""Tests for the livekit Agent the session runs: what its ears let through."""

from collections.abc import AsyncIterator

import pytest
from livekit.agents import (
    AgentStateChangedEvent,
    stt,
)
from livekit.agents.voice import Agent, ModelSettings

from pinecall.session import text
from tests.conftest import postgres
from tests.session.conftest import (
    Box,
    a_session,
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
