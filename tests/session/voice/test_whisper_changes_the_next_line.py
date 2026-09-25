"""The golden: a whisper mid-conversation binds the agent's NEXT line, on a real model."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from livekit.agents import llm as agents

from pinecall._settings import load_settings
from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.providers.models import models_for
from pinecall.session.text.session import TextSession
from pinecall.session.voice.supervising import Supervising
from pinecall.session.voice.writing import Writing
from pinecall.types import NOTHING_BROUGHT, AgentConfig, CallContext, Model, Route
from pinecall_protocol import verbs
from pinecall_protocol.commands import SupervisorVerb
from pinecall_protocol.defs import EndedBy, EndReason, Supervisor
from tests.session.voice.fakes import CALL, Recording

# A voice bridge has no cheap live fixture: nothing in this suite starts a LiveKit room. What the
# whisper actually touches is livekit's own AgentSession and Agent — update_chat_ctx then
# generate_reply — and the text session builds exactly those two, with no TTS under them. So the
# golden runs Supervising against a REAL session on Haiku, which is the same seam a phone call
# uses. See docs/decisions/supervise.md.
pytestmark = pytest.mark.needs_llm

AGENT = "clinica-norte"
HAIKU = Model(provider="anthropic", model="claude-haiku-4-5-20251001")
ANA = Supervisor(id="sup_ab12cd", name="Ana")

# What the whisper carries has to be checkable without asking the model to count or to obey a
# rule its prefix already forbids. Three shapes were tried and each measured something else:
# "answer in exactly three words" landed on four twice in three runs while obeying perfectly
# ("Turno es obligatorio siempre.") — Haiku's arithmetic, not our seam; a nonsense marker word to
# repeat is fought by the note's own last sentence, "Never mention the supervisor or this note",
# which is the half we most want obeyed; and "answer with one word" against a prefix that said
# "en oraciones completas" made the model refuse out loud. What a whisper is actually FOR is a
# fact the model could not know, and the next line either carries it or it does not.
# See docs/decisions/supervise.md.
A_SLOT = "15:40"
THE_LAST_SLOT = (
    f"Hoy queda un solo turno libre, a las {A_SLOT} con la doctora Ferreira. "
    "Ofrecéselo ahora, con la hora."
)

# Deliberately without a rule about HOW to answer. An earlier version of this prefix said
# "respondés en oraciones completas", and Haiku refused the whisper out loud rather than obey it —
# "No puedo seguir esa instrucción. Mi rol es …". A whisper is a later message, not a bigger one:
# a static prefix that forbids what the supervisor asks for wins, and it should. The finding is in
# docs/decisions/supervise.md; what this golden measures is the seam, not that fight.
CLARA = """Sos Clara, la recepcionista de la Clínica Norte.
Atendés al público y conocés los horarios y los turnos."""

# Long enough for one Haiku turn on a bad day, short enough that a hung test is a failed one.
A_TURN = 30.0


async def test_a_whisper_makes_the_very_next_line_obey_it() -> None:
    """The measurement: the next line carries a fact that reached the model only by the whisper."""
    session = await _a_conversation_under_way()
    before = _the_last_line(session)
    assert A_SLOT not in before and A_SLOT not in CLARA, "the slot has to be new"
    desk = Supervising(session.live, session.text_agent, _a_writing(), _NothingEnds())
    coming = _the_next_line(session)
    whisper = verbs.WhisperVerb(verb="whisper", text=THE_LAST_SLOT)
    await desk.apply(SupervisorVerb(by=ANA, verb=whisper))
    line = await asyncio.wait_for(coming, timeout=A_TURN)
    await session.live.aclose()
    assert A_SLOT in line, line
    # And the supervisor stays invisible: the caller is told the fact, never who sent it.
    assert "supervisor" not in line.lower(), line


async def _a_conversation_under_way() -> TextSession:
    """One real exchange with Haiku, so the whisper lands mid-conversation and not at the start."""
    store = MemoryStore()
    context = CallContext(
        call=CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=date.today(),
    )
    config = AgentConfig(slug=AGENT)
    haiku = models_for(load_settings())(HAIKU, NOTHING_BROUGHT)
    session = TextSession(context, config, CallLog(store, AGENT, CALL), haiku)
    await session.start()
    await session.set_prompt("identity", CLARA)
    await session.hears("Hola, ¿atienden los sábados?")
    await session.hears("Perfecto. ¿Y necesito pedir turno antes de ir?")
    return session


def _the_next_line(session: TextSession) -> asyncio.Future[str]:
    """What the agent says next, off livekit's own conversation_item_added."""
    coming: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def added(event: Any) -> None:
        item = event.item
        if isinstance(item, agents.ChatMessage) and item.role == "assistant":
            if not coming.done():
                coming.set_result(item.text_content or "")

    session.live.on("conversation_item_added", added)  # pyright: ignore[reportUnknownMemberType]
    return coming


def _the_last_line(session: TextSession) -> str:
    """What the agent said just before the whisper: what the next line is a change FROM."""
    for item in reversed(session.text_agent.chat_ctx.items):
        if isinstance(item, agents.ChatMessage) and item.role == "assistant":
            return item.text_content or ""
    raise AssertionError("the agent has not said anything yet")


def _a_writing() -> Writing:
    """A Writing over a gateway that records: the entries are not what this golden measures."""
    writing = Writing(Recording(), CALL)
    writing.open()
    return writing


class _NothingEnds:
    """The Ending: no verb in this golden hangs up, and one that tried would say so loudly."""

    async def hangup(
        self, reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False
    ) -> None:
        raise AssertionError(f"a whisper must not end the call ({reason}, {by}, {at_once})")

    def transferred(self) -> None:
        raise AssertionError("a whisper transfers nothing")
