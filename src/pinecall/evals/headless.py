"""One agent's session with no room and no ears: the worker's own path, driven by text alone."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from livekit.agents import stt as recognition
from livekit.agents.types import TimedString
from livekit.agents.voice import AgentSession

from pinecall._settings import Settings, load_settings
from pinecall.evals.answers import Answers
from pinecall.session.declaring import declared
from pinecall.session.voice import session
from pinecall.session.voice.agent import VoiceAgent
from pinecall.session.voice.kit import kit_for
from pinecall.types import NO_ORG_KEYS, AgentConfig, Blocks
from pinecall.types.channel import Channel

# The written door. `session/voice/session.py` builds no STT, no TTS and no VAD for it, which is
# exactly what a ring wants: the turn a text channel takes is the turn a phone call takes once
# the words have been recognised, and nothing here has to fake a microphone to get it.
WRITTEN: Channel = "whatsapp"


# The bridge's side of one turn, with nothing behind it. `Speaking` is the port the agent writes
# through (session/voice/agent.py:16); what was said is already on the run's own events, so it is
# not kept twice, and a written session has no ears to drop a word out of.
class _NoBridge:
    """A Speaking that keeps nothing, standing in for the bridge for the length of one turn."""

    def said(self, delta: str | TimedString) -> None:
        """The reply as the caller hears it. A ring reads it off `RunResult.events` instead."""

    def heard(self, event: recognition.SpeechEvent) -> bool:  # noqa: ARG002 — no ears here
        """Never called: a written session has no stt_node to drop a backchannel out of."""
        return True


@dataclass(frozen=True)
class Headless:
    """One turn's world: the session livekit runs, and the app's answers to what it asks."""

    session: AgentSession[None]
    answers: Answers


@asynccontextmanager
async def a_headless_call(
    config: AgentConfig,
    *,
    prompt: Blocks,
    answers: Answers,
    settings: Settings | None = None,
) -> AsyncGenerator[Headless]:
    """The worker's own config → kit → session, started with no room and closed after."""
    kit = kit_for(settings or load_settings())
    # The box's own vendor keys: a headless call belongs to no org, so it brought none.
    live = session.a_session(config, kit, WRITTEN, NO_ORG_KEYS)
    # The prompt arrives the way the app sends it at call start, already written into its blocks:
    # the static ones become livekit's `instructions` — the pinned item at index 0 the provider's
    # cache lands on — and the dynamic ones are read per request, after the history. Never
    # reordered; a ring renders once and holds it, because nothing here moves the state.
    agent = VoiceAgent(
        blocks=prompt,
        tools=declared(config.tools, answers),
        speaking=_NoBridge(),
    )
    await live.start(agent)  # pyright: ignore[reportUnknownMemberType] — livekit's start is untyped
    try:
        yield Headless(session=live, answers=answers)
    finally:
        await live.aclose()
        await _close_the_vendors(live)


# `AgentSession.aclose` closes the session, never the vendors: a worker process holds its models
# for the length of a job and then exits, so the library leaves their lifetime to whoever built
# them. A test process does not exit, and an HTTP client nobody closed comes back as a
# ResourceWarning — which this suite treats as an error. The ring built them, so the ring closes
# them, through the session's own accessors (agent_session.py:2103,2107,2111).
async def _close_the_vendors(live: AgentSession[None]) -> None:
    """Every model this call was built with, shut down in the order a session names them."""
    vendors = cast("tuple[Any, ...]", (live.stt, live.llm, live.tts))  # pyright: ignore[reportUnknownMemberType]
    for vendor in vendors:
        if vendor is not None:
            await vendor.aclose()
