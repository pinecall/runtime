"""One session for a spoken call and a written one: its commands, its people, its tools, its end."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import av
import pytest
from livekit import rtc
from livekit.agents import (
    AgentStateChangedEvent,
    APIStatusError,
    CloseEvent,
    CloseReason,
    ConversationItemAddedEvent,
    RunContext,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    llm,
    metrics,
    stt,
)
from livekit.agents import ErrorEvent as ComponentFailed
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.language import LanguageCode
from livekit.agents.llm.tool_context import ToolFlag
from livekit.agents.types import TimedString
from livekit.agents.voice import ModelSettings
from livekit.protocol.sip import SIPOutboundConfig

import pinecall
from pinecall.domain.agent import (
    AgentConfig,
    Greeting,
    Hangup,
    MemoryPolicy,
    ToolSpec,
)
from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotAvailable
from pinecall.domain.names import JsonObject
from pinecall.log.store import Store
from pinecall.providers.build import Running, TurnModel
from pinecall.providers.credentials import Pipeline
from pinecall.session import hold as hold_module
from pinecall.session import text
from pinecall.session._hearing import keyterms
from pinecall.session._prompt import A_RELEASE
from pinecall.session.call import Call, Platform, ToolUse
from pinecall.session.hold import HoldMusic
from pinecall.session.room import CALLER_NUMBER, CallRoom, Legs, Trunk
from pinecall.session.session import SAY_GOODBYE_FIRST, Session
from pinecall.session.voice import voice_session
from pinecall.wire.commands import (
    AgentReply,
    AgentSay,
    CallAttention,
    CallCallback,
    CallEvent,
    CallHangup,
    CallHold,
    CallLog,
    CallTransfer,
    CallUnhold,
    EndVerb,
    PromptSet,
    ReleaseVerb,
    SayVerb,
    SessionConfigure,
    StateSet,
    SupervisorVerb,
    TakeoverVerb,
    ToolsSet,
    TransferVerb,
    WhisperVerb,
)
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import Supervisor, ToolResult
from pinecall.wire.parts import ToolSpec as WiredTool
from tests.conftest import postgres
from tests.fakes.acme import ACME, seat
from tests.fakes.livekit import Microphone, Player, Server, Speaker, tone
from tests.fakes.livekit import Room as AnOfflineRoom
from tests.session.conftest import (
    THE_CALLER,
    Box,
    a_session,
    context_of,
    heard_live,
    kinds,
    model_of,
)

A_SUPERVISOR = Supervisor(id="mem_1", name="Ana")
NOBODY = AgentConfig(slug="clinica-norte")
BOOK = ToolSpec(
    "book",
    "Book a table for a day.",
    {"type": "object", "properties": {"day": {"type": "string"}}},
    confirm="Reservado para el {{day}}, {{result.table}} {{result.missing}}.",
)
CANCEL = ToolSpec("cancel", "Cancel the booking.", {"type": "object", "properties": {}})
BOOKING = AgentConfig(
    slug="clinica-norte",
    tools=(BOOK, CANCEL),
    events={"paid": frozenset({"app"}), "clicked": frozenset({"participant"})},
)


def supervised(
    verb: SayVerb | WhisperVerb | TakeoverVerb | ReleaseVerb | EndVerb | TransferVerb,
) -> SupervisorVerb:
    return SupervisorVerb(by=A_SUPERVISOR, verb=verb)


async def settled() -> None:
    """Let livekit's tasks and the call's queue run."""
    await asyncio.sleep(0.05)


# ── the opening ──


@postgres
async def test_a_call_starts_with_call_started_then_the_knowledge_it_ships_with(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", knowledge="# Abrimos a las nueve"))
    await session.start()
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    assert [entry.type for entry in entries][:2] == ["call.started", "prompt.changed"]
    assert entries[1].data["name"] == "knowledge"
    assert "Abrimos" not in str(entries[1].data)


@postgres
async def test_a_call_opened_with_no_room_starts_as_a_written_one(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte"))
    await session.start()
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    assert entries[0].data["medium"] == "text"


@postgres
async def test_a_class_that_ships_no_file_writes_no_line_about_one(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await text.end(session, "caller_hung_up", "caller")
    assert "prompt.changed" not in await kinds(store, call)


@postgres
async def test_words_run_the_verbatim_verb_and_nothing_else(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(
        box, AgentConfig(slug="clinica-norte", greeting=Greeting(say="Buenas, clínica."))
    )
    await session.start()
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent == ["Buenas, clínica."]
    assert model_of(session).requests == []


@postgres
async def test_an_instruction_runs_the_model_and_never_the_verbatim_verb(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(
        box,
        AgentConfig(slug="clinica-norte", greeting=Greeting(reply="Saluda")),
        ["Hola, ¿qué necesita?"],
    )
    await session.start()
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert len(model_of(session).requests) == 1
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent == ["Hola, ¿qué necesita?"]


@postgres
async def test_the_opening_is_said_before_the_greeting_and_logged_as_the_agents_turn(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", greeting=Greeting(say="Buenas")))
    await session.start(opening="Le habla un asistente automático en nombre de Clínica Norte.")
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent == ["Le habla un asistente automático en nombre de Clínica Norte.", "Buenas"]


@postgres
async def test_a_call_a_run_opened_is_not_greeted_at_all(box: Box, store: Store, call: str) -> None:
    greeted = AgentConfig(slug="clinica-norte", greeting=Greeting(say="Buenas"))
    session = a_session(box, greeted, run="run_1")
    await session.start()
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert "turn.agent" not in await kinds(store, call)


# ── the app's commands ──


@postgres
async def test_agent_say_is_the_sessions_say_verbatim_and_enters_the_history(box: Box) -> None:
    session = a_session(box, NOBODY, ["Sí, lo dije"])
    await session.start()
    await session.apply(AgentSay(text="Su turno es el lunes."))
    await settled()
    await text.hears(session, "¿qué dijo?")
    sentence = [getattr(item, "text_content", "") for item in model_of(session).requests[0].items]
    assert "Su turno es el lunes." in sentence
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_agent_reply_is_one_model_turn_guided_by_the_instruction(box: Box) -> None:
    session = a_session(box, NOBODY, ["Le confirmo el turno"])
    await session.start()
    await session.apply(AgentReply(instructions="Confirma el turno"))
    await settled()
    requests = model_of(session).requests
    assert len(requests) == 1
    assert "Confirma el turno" in str(
        [getattr(item, "text_content", "") for item in requests[0].items]
    )
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_the_same_instructions_twice_is_not_a_rewrite(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(PromptSet(name="identity", text="Hola"))
    before = session.agent.instructions
    assert not session.blocks.set("identity", "Hola")
    assert session.agent.instructions == before
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_the_view_reaches_the_model_last_and_never_touches_the_instructions(box: Box) -> None:
    session = a_session(box, NOBODY, ["ok"])
    await session.start()
    await session.apply(PromptSet(name="view", text="Turnos libres: lunes"))
    await text.hears(session, "¿hay turno?")
    await text.end(session, "caller_hung_up", "caller")
    items = model_of(session).requests[0].items
    assert "Turnos libres" in str(getattr(items[-1], "text_content", ""))
    assert "Turnos libres" not in str(session.agent.instructions)


@postgres
async def test_the_state_the_app_set_lands_whole_with_what_changed(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(StateSet(state={"patient": {"name": "Ana Pérez"}, "step": 2}))
    await text.end(session, "caller_hung_up", "caller")
    changed = next(entry for entry in await store.whole(call) if entry.type == "state.changed")
    assert changed.data["state"] == {"patient": {"name": "Ana Pérez"}, "step": 2}
    assert changed.data["changed"] == ["patient", "step"]


@postgres
async def test_a_session_configure_declares_for_the_call_and_sets_its_state(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(SessionConfigure(config=Declared(language="en-US"), state={"step": 1}))
    await text.end(session, "caller_hung_up", "caller")
    assert session.call.config.language == "en-US"
    assert {"agent.configured", "state.changed"} <= set(await kinds(store, call))


@postgres
async def test_an_event_the_app_sent_is_the_cause_of_the_next_state(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, BOOKING)
    await session.start()
    await session.apply(CallEvent(name="paid", data={"eur": 30}))
    await session.apply(StateSet(state={"paid": True}))
    await session.apply(StateSet(state={"paid": True, "again": 1}))
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    received = next(entry for entry in entries if entry.type == "event.received")
    states = [entry.data for entry in entries if entry.type == "state.changed"]
    assert states[0]["cause"] == {"kind": "event", "name": "paid", "seq": received.seq}
    assert "cause" not in states[1]


@postgres
async def test_an_event_the_agent_did_not_declare_for_its_sender_is_refused(box: Box) -> None:
    session = a_session(box, BOOKING)
    await session.start()
    with pytest.raises(DeclarationRefused, match="'clicked' is not one"):
        await session.apply(CallEvent(name="clicked", data={}))
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_call_log_and_call_callback_are_written_where_the_call_runs(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallLog(name="crm", data={"ticket": 7}))
    await session.apply(CallCallback(number="+59899000111", when="mañana"))
    await text.end(session, "caller_hung_up", "caller")
    entries = {entry.type: entry.data for entry in await store.whole(call)}
    assert entries["custom"] == {"name": "crm", "data": {"ticket": 7}}
    back = entries["callback.requested"]
    assert (back["via"], back["call"], back["number"]) == ("agent", call, "+59899000111")


@postgres
async def test_call_hangup_ends_the_call_as_the_agent(box: Box, store: Store, call: str) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallHangup())
    await session.close()
    ended = next(entry for entry in await store.whole(call) if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("agent_hung_up", "agent")


@postgres
async def test_a_room_verb_on_a_call_with_no_room_is_refused_by_name(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    with pytest.raises(NotAllowed, match="CallTransfer is not something this call does"):
        await session.apply(CallTransfer(to="+59829000000"))
    await text.end(session, "caller_hung_up", "caller")


# ── tools ──


@postgres
async def test_a_tool_round_trips_through_the_platform_and_the_model_reads_its_answer(
    box: Box,
) -> None:
    box.answers["book"] = ToolResult(call_id="t1", name="book", output={"table": "mesa 4"})
    session = a_session(
        box, BOOKING, [{"name": "book", "arguments": {"day": "lunes"}, "call_id": "t1"}], ["Listo"]
    )
    await session.start()
    sentence = await text.hears(session, "reserva el lunes")
    await text.end(session, "caller_hung_up", "caller")
    assert [(use.name, use.arguments) for use in box.used] == [("book", {"day": "lunes"})]
    assert '"table": "mesa 4"' in _output_of(session, "t1").output
    assert sentence == "Listo"


@postgres
async def test_a_confirm_tool_reads_back_what_was_done_before_the_model_answers(
    box: Box, store: Store, call: str
) -> None:
    box.answers["book"] = ToolResult(call_id="t1", name="book", output={"table": "mesa 4"})
    session = a_session(
        box,
        BOOKING,
        [{"name": "book", "arguments": {"day": "lunes"}, "call_id": "t1"}],
        ["Algo más?"],
    )
    await session.start()
    await text.hears(session, "reserva el lunes")
    await text.end(session, "caller_hung_up", "caller")
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent[0] == "Reservado para el lunes, mesa 4 {{result.missing}}."
    history = [getattr(item, "text_content", "") for item in model_of(session).requests[1].items]
    assert agent[0] in history


@postgres
async def test_a_tool_that_failed_is_an_error_the_model_reads_and_no_read_back(
    box: Box, store: Store, call: str
) -> None:
    box.answers["book"] = ToolResult(call_id="t1", name="book", error="no hay mesas")
    session = a_session(
        box,
        BOOKING,
        [{"name": "book", "arguments": {"day": "lunes"}, "call_id": "t1"}],
        ["Lo siento"],
    )
    await session.start()
    await text.hears(session, "reserva el lunes")
    await text.end(session, "caller_hung_up", "caller")
    output = _output_of(session, "t1")
    assert (output.output, output.is_error) == ("no hay mesas", True)
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent == ["Lo siento"]


@postgres
async def test_a_tool_the_app_closed_is_refused_before_the_app_and_the_model_reads_why(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, BOOKING, [{"name": "cancel", "call_id": "t9"}], ["No puedo cancelar"])
    await session.start()
    await session.apply(
        ToolsSet(tools=[WiredTool(name="book", description="d", parameters={"type": "object"})])
    )
    await text.hears(session, "cancelá")
    await text.end(session, "caller_hung_up", "caller")
    assert box.used == []
    entries = await store.whole(call)
    assert next(entry for entry in entries if entry.type == "tools.changed").data == {
        "visible": ["book"]
    }
    refused = next(entry for entry in entries if entry.type == "error")
    assert (refused.data["code"], refused.data["message"]) == (
        "refused",
        "cancel is not available now",
    )
    assert _output_of(session, "t9").output == "cancel is not available now"


@postgres
async def test_a_tools_set_between_two_requests_leaves_the_providers_tools_byte_identical(
    box: Box,
) -> None:
    session = a_session(box, BOOKING, ["uno"], ["dos"])
    await session.start()
    await text.hears(session, "hola")
    await session.apply(ToolsSet(tools=[]))
    await text.hears(session, "hola otra vez")
    await text.end(session, "caller_hung_up", "caller")
    first, second = model_of(session).requests
    assert first.tools == second.tools


@postgres
async def test_a_state_set_during_a_tool_cites_the_tool_as_its_cause(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(
        box, BOOKING, [{"name": "book", "arguments": {"day": "lunes"}, "call_id": "t1"}], ["ok"]
    )

    async def answering(_use: ToolUse, _speech: str | None) -> ToolResult:
        await session.apply(StateSet(state={"booked": True}))
        return ToolResult(call_id="t1", name="book", output="ok")

    session.call.platform = Platform(
        append_many=box.log.append_many, tool=answering, lookup=box.lookup, seal=box.seal
    )
    await session.start()
    await text.hears(session, "reserva")
    await text.end(session, "caller_hung_up", "caller")
    state = next(entry for entry in await store.whole(call) if entry.type == "state.changed")
    assert state.data["cause"] == {"kind": "tool", "tool": "book", "call_id": "t1"}


# ── a person on the line ──


@postgres
async def test_holding_a_call_writes_the_line_once_and_giving_it_back_writes_it_again(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallHold())
    await session.apply(CallHold())
    await session.apply(CallUnhold())
    await text.end(session, "caller_hung_up", "caller")
    lines = [entry.data for entry in await store.whole(call) if entry.type == "call.line"]
    assert lines == [{"held": True, "muted": False}, {"held": False, "muted": False}]


@postgres
async def test_an_ask_puts_the_caller_on_hold_and_says_what_a_supervisor_is_wanted_for(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallAttention(reason="quiere hablar con un médico", wait_s=30))
    assert session.call.a_person_has_the_line
    with pytest.raises(DeclarationRefused, match="already waiting"):
        await session.apply(CallAttention(reason="otra vez", wait_s=30))
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    found = next(entry for entry in entries if entry.type == "attention.requested")
    assert found.data == {"reason": "quiere hablar con un médico", "wait_s": 30.0}
    assert [entry.data["held"] for entry in entries if entry.type == "call.line"] == [True]


@postgres
async def test_a_supervisor_taking_the_line_answers_the_ask(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallAttention(reason="ayuda", wait_s=30))
    await session.apply(supervised(TakeoverVerb()))
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    answered = next(entry for entry in entries if entry.type == "attention.answered")
    assert answered.data["ok"] is True
    assert answered.data["by"] == {"id": "mem_1", "name": "Ana"}
    assert session.call.taken_by == A_SUPERVISOR
    assert not session.call.waiting_for_a_person


@postgres
async def test_a_wait_nobody_answered_gives_the_line_back_to_the_agent(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallAttention(reason="ayuda", wait_s=0.05))
    await asyncio.sleep(0.2)
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    answered = next(entry for entry in entries if entry.type == "attention.answered")
    assert (answered.data["ok"], answered.data["error"]) == (
        False,
        "nobody took the line within 0.05s",
    )
    assert [entry.data["held"] for entry in entries if entry.type == "call.line"] == [True, False]
    assert not session.call.a_person_has_the_line


@postgres
async def test_say_writes_who_asked_before_the_agent_says_a_word(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(supervised(SayVerb(text="Un momento, por favor.")))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    written = await kinds(store, call)
    assert written.index("supervisor.said") < written.index("turn.agent")


@postgres
async def test_a_whisper_lands_in_the_history_and_asks_for_the_next_turn(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["Le ofrezco el martes"])
    await session.start()
    await session.apply(supervised(WhisperVerb(text="ofrecele el martes")))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    (request,) = model_of(session).requests
    notes = [str(getattr(item, "text_content", "")) for item in request.items]
    assert any("ofrecele el martes" in note for note in notes)
    assert "ofrecele" not in str(session.agent.instructions)
    assert "supervisor.whispered" in await kinds(store, call)


@postgres
async def test_a_whisper_while_a_human_holds_the_line_never_talks_over_them(box: Box) -> None:
    session = a_session(box, NOBODY, ["nunca"])
    await session.start()
    await session.apply(supervised(TakeoverVerb()))
    await session.apply(supervised(WhisperVerb(text="nada")))
    await settled()
    assert model_of(session).requests == []
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_a_second_takeover_is_refused_and_names_who_holds_the_line(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(supervised(TakeoverVerb()))
    with pytest.raises(DeclarationRefused, match="mem_1 already holds the line"):
        await session.apply(supervised(TakeoverVerb()))
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_a_release_gives_the_line_back_with_a_note_that_never_guesses(box: Box) -> None:
    session = a_session(box, NOBODY, ["¿Seguimos?"])
    await session.start()
    await session.apply(supervised(TakeoverVerb()))
    await session.apply(supervised(ReleaseVerb()))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert session.call.taken_by is None
    notes = [str(getattr(item, "text_content", "")) for item in model_of(session).requests[0].items]
    assert A_RELEASE in notes
    assert "Do not guess" in A_RELEASE


@postgres
async def test_a_release_nobody_asked_for_is_refused_and_writes_nothing(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    with pytest.raises(DeclarationRefused, match="nobody holds the line"):
        await session.apply(supervised(ReleaseVerb()))
    await text.end(session, "caller_hung_up", "caller")
    assert "supervisor.released" not in await kinds(store, call)


@postgres
async def test_the_end_verb_hangs_up_as_the_supervisor_and_never_as_the_agent(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(supervised(EndVerb(reason="spam")))
    await session.close()
    entries = {entry.type: entry.data for entry in await store.whole(call)}
    assert entries["supervisor.ended"]["reason"] == "spam"
    assert (entries["call.ended"]["reason"], entries["call.ended"]["ended_by"]) == (
        "supervisor_ended",
        "supervisor",
    )


@postgres
async def test_a_transfer_on_a_written_call_is_refused_before_anything_is_written(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    with pytest.raises(NotAllowed):
        await session.apply(supervised(TransferVerb(to="+59829000000")))
    await session.close()
    assert "supervisor.transferred" not in await kinds(store, call)


# ── what livekit tells the session ──


@postgres
async def test_an_interim_is_an_ephemeral_transcript_and_starts_the_lookups(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, AgentConfig(slug="clinica-norte", memory=MemoryPolicy()))
    await session.start()
    sentence = "quiero cambiar mi turno del lunes"
    session.live.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript=sentence, is_final=False, language=LanguageCode("es")),
    )
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    heard = [entry for entry in seen if entry.type == "user.transcript"]
    assert (heard[0].data["final"], heard[0].ephemeral) == (False, True)
    assert box.lookups == [("recall", {"contact": "+59899123456", "query": sentence})]


@postgres
async def test_a_turn_too_short_to_start_a_run_starts_none_before_the_turn_ends(box: Box) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", memory=MemoryPolicy()))
    await session.start()
    session.live.emit(
        "user_input_transcribed", UserInputTranscribedEvent(transcript="sí, eso", is_final=False)
    )
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert box.lookups == []


@postgres
async def test_a_voice_that_does_not_exist_ends_the_call_after_one_error_and_no_retry(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    refused = APIStatusError("voice not found", status_code=404)
    failed = ComponentFailed(error=_component_error(refused), source=model_of(session))
    session.live.emit("error", failed)
    session.live.emit("error", failed)
    await session.close()
    entries = await store.whole(call)
    errors = [entry.data["code"] for entry in entries if entry.type == "error"]
    assert errors == ["component_dead_end"]
    ended = next(entry for entry in entries if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("error", "platform")


@postgres
async def test_a_vendor_having_a_bad_minute_is_written_and_the_call_goes_on(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    busy = APIStatusError("overloaded", status_code=529)
    session.live.emit(
        "error",
        ComponentFailed(error=_component_error(busy, recoverable=True), source=model_of(session)),
    )
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    errors = [entry.data for entry in await store.whole(call) if entry.type == "error"]
    assert (errors[0]["code"], errors[0]["recoverable"]) == ("component_failed", True)
    ended = next(entry for entry in await store.whole(call) if entry.type == "call.ended")
    assert ended.data["reason"] == "caller_hung_up"


@postgres
async def test_an_agent_turn_carries_its_report_whole_and_whether_it_was_cut_off(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    sentence = llm.ChatMessage(
        role="assistant", content=["Hasta"], interrupted=True, metrics={"llm_node_ttft": 0.3}
    )
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=sentence))
    await text.end(session, "caller_hung_up", "caller")
    turn = next(entry for entry in await store.whole(call) if entry.type == "turn.agent")
    assert (turn.data["text"], turn.data["interrupted"]) == ("Hasta", True)
    assert turn.data["metrics"] == {"llm_node_ttft": 0.3}


# ── a spoken call's session ──


def test_the_declared_words_come_first_and_the_state_adds_the_names_it_holds() -> None:
    config = AgentConfig(slug="clinica-norte", hears=("Vidal",))
    state: JsonObject = {
        "patient": {"name": "Ana Pérez", "phone": "+59899123456"},
        "note": "x " * 30,
        "who": "Vidal",
    }
    assert keyterms(config, state) == ["Vidal", "Ana Pérez"]


# ── end_call ──


@postgres
async def test_a_class_that_declares_no_hangup_gets_no_tool(box: Box) -> None:
    session = a_session(box, NOBODY)
    assert not any(isinstance(tool, EndCallTool) for tool in session.agent.tools)


@postgres
async def test_the_tenants_words_and_the_goodbye_reach_livekits_own_end_call(box: Box) -> None:
    session = a_session(
        box, AgentConfig(slug="clinica-norte", hangup=Hangup(when="when they say bye"))
    )
    (ending,) = [tool for tool in session.agent.tools if isinstance(tool, EndCallTool)]
    (end_call,) = ending.tools
    assert isinstance(end_call, llm.FunctionTool)
    described = end_call.info.description or ""
    assert "when they say bye" in described
    assert SAY_GOODBYE_FIRST in described
    # Hidden while the agent greets, or a model hangs up before the caller speaks.
    assert end_call.info.flags & ToolFlag.IGNORE_ON_ENTER


def spoken_call(
    box: Box,
    config: AgentConfig,
    *,
    ends_the_turn: bool = False,
    keyterms: bool = False,
    turn_model: TurnModel = "v1-mini",
) -> Session:
    assert box.log.call is not None
    call = Call(context_of(box.log.call, "phone"), config, box.platform())
    ears: JsonObject = {"keyterms": True} if keyterms else {}
    stages = Pipeline(
        llm=Running(ACME, "k"),
        stt=Running(ACME, "k", ends_the_turn=ends_the_turn, options=ears, turn_model=turn_model),
        tts=Running(ACME, "k"),
    )
    return voice_session(call, stages)


def _component_error(error: Exception, *, recoverable: bool = False) -> object:
    return llm.LLMError(timestamp=time.time(), label="acme", error=error, recoverable=recoverable)


def _output_of(session: Session, call_id: str) -> llm.FunctionCallOutput:
    items = model_of(session).requests[-1].items
    return next(
        item
        for item in items
        if isinstance(item, llm.FunctionCallOutput) and item.call_id == call_id
    )


# ── what the agent says, piece by piece ──


@postgres
async def test_an_aligned_reply_is_logged_word_by_word_with_livekits_own_timings(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    words = [
        TimedString("Hola", start_time=0.0, end_time=0.3),
        TimedString(" Ana", start_time=0.3, end_time=0.6),
    ]
    passed = [
        piece async for piece in session.agent.transcription_node(_pieces(*words), ModelSettings())
    ]
    await text.end(session, "caller_hung_up", "caller")
    assert passed == words
    sentence = [entry.data for entry in seen if entry.type == "agent.transcript"]
    assert [(item["text"], item.get("start"), item.get("end")) for item in sentence] == [
        ("Hola", 0.0, 0.3),
        (" Ana", 0.3, 0.6),
    ]


@postgres
async def test_a_voice_that_aligned_nothing_leaves_the_timings_off_the_entry(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    async for _ in session.agent.transcription_node(_pieces("Hola"), ModelSettings()):
        pass
    await text.end(session, "caller_hung_up", "caller")
    (sentence,) = [entry.data for entry in seen if entry.type == "agent.transcript"]
    assert "start" not in sentence
    assert "end" not in sentence


# ── what the caller says ──


@postgres
async def test_the_callers_words_and_states_land_in_the_order_they_were_heard(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    session.live.emit(
        "user_state_changed", UserStateChangedEvent(old_state="listening", new_state="speaking")
    )
    session.live.emit(
        "user_input_transcribed", UserInputTranscribedEvent(transcript="hola", is_final=False)
    )
    session.live.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="listening", new_state="thinking")
    )
    await text.end(session, "caller_hung_up", "caller")
    order = [
        entry.type
        for entry in seen
        if entry.type in {"user.state", "user.transcript", "agent.state"}
    ]
    assert order[-3:] == ["user.state", "user.transcript", "agent.state"]


@postgres
async def test_only_the_interim_transcript_reaches_the_lookups_and_never_the_final(
    box: Box,
) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", memory=MemoryPolicy()))
    await session.start()
    final = UserInputTranscribedEvent(transcript="quiero cambiar mi turno del lunes", is_final=True)
    session.live.emit("user_input_transcribed", final)
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert box.lookups == []


@postgres
async def test_an_agent_reply_is_no_query_and_asks_nobody(box: Box) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", memory=MemoryPolicy()), ["ok"])
    await session.start()
    await session.apply(AgentReply(instructions="Ofrecé el martes a las diez de la mañana"))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert box.lookups == []


# ── a barge-in ──


# The tool is livekit's to await after a cut (it may have booked already), so the turn is over
# when the app answered; what is ours must be gone by then, but the call's writer.
@postgres
async def test_a_caller_cutting_in_mid_sentence_leaves_no_task_of_ours_behind_the_turn(
    box: Box, store: Store, call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hold_module, "GRACE_S", 0.01)
    answered = asyncio.Event()
    session = _a_slow_cancellation(box, answered)
    speaker, melody = Speaker(), HoldMusic(Path("hold.ogg"))
    melody.player = Player()
    await session.start(hold=melody)
    session.live.output.audio = speaker
    session.live.generate_reply(user_input="cancelá mi reserva")
    await settled()
    assert speaker.playing
    heard = UserInputTranscribedEvent(transcript="no espere quiero cambiarla", is_final=False)
    session.live.emit("user_input_transcribed", heard)
    await session.live.interrupt()
    answered.set()
    await settled()
    left = [task for task in asyncio.all_tasks() if _ours(task)]
    assert left == [session.call.writing.draining]
    assert (speaker.cut, melody.running, melody.pending) == (1, 0, None)
    assert all(handle.done() for handle in melody.player.handles)
    await session.close()
    turn = next(entry for entry in await store.whole(call) if entry.type == "turn.agent")
    assert turn.data["interrupted"] is True


# ── the ears' words ──


def test_an_agent_that_declared_nothing_and_holds_nothing_asks_for_nothing() -> None:
    assert keyterms(NOBODY, {}) == []


# ── the recording ──


@postgres
async def test_a_spoken_calls_recording_is_whole_before_the_seal_names_it(
    box: Box, tmp_path: Path
) -> None:
    assert box.log.call is not None
    audio = tmp_path / "audio.ogg"
    channels_at_the_seal: list[int] = []

    async def seal(usage: list[ModelUsage], outcome: str) -> None:
        with av.open(str(audio)) as container:
            channels_at_the_seal.append(container.streams.audio[0].channels)
        await box.seal(usage, outcome)

    platform = Platform(
        append_many=box.log.append_many, tool=box.tool, lookup=box.lookup, seal=seal
    )
    stages = Pipeline(llm=Running(ACME, "k"), stt=Running(ACME, "k"), tts=Running(ACME, "k"))
    session = voice_session(
        Call(context_of(box.log.call, "phone"), NOBODY, platform, audio), stages
    )
    session.live.input.audio = Microphone(tone(300, 0.2))
    session.live.output.audio = Speaker()
    await session.start()
    assert session.recorder is not None
    await session.close()
    assert channels_at_the_seal == [2]


# ── the line ──


@postgres
async def test_a_held_call_leaves_the_agent_neither_speaking_nor_hearing(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallHold())
    assert (session.live.input.audio_enabled, session.live.output.audio_enabled) == (False, False)
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_a_takeover_cuts_the_sentence_and_leaves_the_agent_mute_and_deaf(
    box: Box, store: Store, call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    cut: list[bool] = []

    async def interrupted(*, force: bool = False) -> None:
        cut.append(force)

    monkeypatch.setattr(session.live, "interrupt", interrupted)
    await session.apply(supervised(TakeoverVerb()))
    assert cut == [True]
    assert (session.live.input.audio_enabled, session.live.output.audio_enabled) == (False, False)
    await text.end(session, "caller_hung_up", "caller")
    assert "supervisor.took_over" in await kinds(store, call)


@postgres
async def test_a_supervisor_taking_a_line_on_plain_hold_ends_the_hold_for_them(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallHold())
    await session.apply(supervised(TakeoverVerb()))
    await text.end(session, "caller_hung_up", "caller")
    lines = [entry.data["held"] for entry in await store.whole(call) if entry.type == "call.line"]
    assert lines == [True, False]
    assert session.call.taken_by == A_SUPERVISOR
    assert not session.on_hold


@postgres
async def test_every_one_of_the_six_verbs_lands_in_the_callers_log(
    box: Box, store: Store, call: str, server: Server
) -> None:
    session = spoken_call(box, NOBODY)
    await session.start(where=_a_room(box, session.call, server))
    for verb in (
        SayVerb(text="Un momento"),
        WhisperVerb(text="ofrecé el martes"),
        TakeoverVerb(),
        ReleaseVerb(),
    ):
        await session.apply(supervised(verb))
    await session.apply(supervised(TransferVerb(to="+59829000000", mode="cold")))
    await session.apply(supervised(EndVerb(reason="listo")))
    await session.close()
    written = await kinds(store, call)
    for kind in (
        "supervisor.said",
        "supervisor.whispered",
        "supervisor.took_over",
        "supervisor.released",
        "supervisor.transferred",
        "supervisor.ended",
    ):
        assert kind in written


@postgres
async def test_a_transfer_is_written_here_and_finished_by_the_transfer_applier(
    box: Box, store: Store, call: str, server: Server
) -> None:
    session = a_session(box, NOBODY)
    await session.start(where=_a_room(box, session.call, server))
    await session.apply(supervised(TransferVerb(to="+59829000000")))
    await session.close()
    entries = await store.whole(call)
    written = [entry.type for entry in entries]
    assert written.index("supervisor.transferred") < written.index("call.transferred")
    found = next(entry for entry in entries if entry.type == "supervisor.transferred")
    assert found.data["mode"] == "cold"
    ended = next(entry for entry in entries if entry.type == "call.ended")
    assert ended.data["reason"] == "transferred"


# ── tools ──


@postgres
async def test_the_receipt_does_not_make_the_model_answer_itself(
    box: Box, store: Store, call: str
) -> None:
    box.answers["book"] = ToolResult(call_id="t1", name="book", output={"table": "mesa 4"})
    session = a_session(
        box,
        BOOKING,
        ["Le reservo", {"name": "book", "arguments": {"day": "lunes"}, "call_id": "t1"}],
        ["¿Algo más?"],
    )
    await session.start()
    await text.hears(session, "reserva el lunes")
    await text.end(session, "caller_hung_up", "caller")
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent == [
        "Le reservo",
        "Reservado para el lunes, mesa 4 {{result.missing}}.",
        "¿Algo más?",
    ]
    assert len(model_of(session).requests) == 2


@postgres
async def test_a_platform_that_refuses_is_a_tool_that_did_not_answer_and_the_call_goes_on(
    box: Box,
) -> None:
    async def refusing(_use: ToolUse, _speech: str | None) -> ToolResult:
        raise NotAvailable("the app is not connected")

    session = a_session(
        box, BOOKING, [{"name": "book", "arguments": {}, "call_id": "t1"}], ["Probemos luego"]
    )
    session.call.platform = Platform(
        append_many=box.log.append_many, tool=refusing, lookup=box.lookup, seal=box.seal
    )
    await session.start()
    sentence = await text.hears(session, "reserva")
    await text.end(session, "caller_hung_up", "caller")
    output = _output_of(session, "t1")
    assert (output.output, output.is_error) == ("the app is not connected", True)
    assert sentence == "Probemos luego"


@postgres
async def test_the_tool_runs_after_its_announcement_with_or_without_a_receipt(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    happened: list[str] = []

    async def played(_context: RunContext[None]) -> None:
        happened.append("played")

    monkeypatch.setattr(RunContext, "wait_for_playout", played)
    answering = box.tool

    async def recording(use: ToolUse, speech: str | None) -> ToolResult:
        happened.append(use.name)
        return await answering(use, speech)

    session = a_session(
        box,
        BOOKING,
        [{"name": "book", "arguments": {}, "call_id": "t1"}, {"name": "cancel", "call_id": "t2"}],
        ["hecho"],
    )
    session.call.platform = Platform(
        append_many=box.log.append_many, tool=recording, lookup=box.lookup, seal=box.seal
    )
    await session.start()
    await text.hears(session, "reservá y cancelá")
    await text.end(session, "caller_hung_up", "caller")
    assert happened.count("played") == 2
    assert happened.index("played") < happened.index("book")


# ── the end ──


@postgres
async def test_a_worker_the_platform_took_down_is_drained_and_never_an_error(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    session.live.emit("close", CloseEvent(reason=CloseReason.JOB_SHUTDOWN))
    await session.close()
    ended = next(entry for entry in await store.whole(call) if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("drained", "platform")


@postgres
async def test_letting_go_of_the_session_leaves_no_listener_behind(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    await text.end(session, "caller_hung_up", "caller")
    before = len(seen)
    session.live.emit(
        "user_state_changed", UserStateChangedEvent(old_state="listening", new_state="away")
    )
    model_of(session).emit("metrics_collected", a_block())
    await settled()
    assert len(seen) == before


@postgres
async def test_end_call_is_in_the_log_like_any_other_tool_and_its_reason_comes_first(
    box: Box, store: Store, call: str
) -> None:
    ending = AgentConfig(slug="clinica-norte", hangup=Hangup(when="cuando se despida"))
    session = a_session(box, ending, ["Chau", {"name": "end_call", "call_id": "e1"}], ["nunca"])
    await session.start()
    await text.hears(session, "chau, gracias")
    await asyncio.sleep(0.2)
    await session.close()
    entries = await store.whole(call)
    tools = [
        (entry.type, entry.data["name"])
        for entry in entries
        if entry.type in {"tool.call", "tool.result"}
    ]
    assert tools == [("tool.call", "end_call"), ("tool.result", "end_call")]
    ended = next(entry for entry in entries if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("agent_hung_up", "agent")


@postgres
async def test_nothing_is_generated_after_end_call(box: Box) -> None:
    ending = AgentConfig(slug="clinica-norte", hangup=Hangup())
    session = a_session(box, ending, ["Chau", {"name": "end_call", "call_id": "e1"}], ["nunca"])
    await session.start()
    await text.hears(session, "chau")
    await asyncio.sleep(0.2)
    await session.close()
    assert len(model_of(session).requests) == 1


# ── metrics ──


@postgres
async def test_the_ticks_of_a_streaming_stt_ride_the_stream_and_one_measured_once_is_stored(
    box: Box,
) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    model_of(session).emit("metrics_collected", _heard_block(streamed=True))
    model_of(session).emit("metrics_collected", _heard_block(streamed=False))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    blocks = [
        (entry.data["streamed"], entry.ephemeral) for entry in seen if entry.type == "metrics.stt"
    ]
    assert blocks == [(True, True), (False, False)]


@postgres
async def test_the_turns_own_transcription_delay_is_what_a_reader_reads(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    heard = llm.ChatMessage(role="user", content=["hola"], metrics={"transcription_delay": 0.12})
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=heard))
    await text.end(session, "caller_hung_up", "caller")
    turn = next(entry for entry in await store.whole(call) if entry.type == "turn.user")
    assert turn.data["metrics"] == {"transcription_delay": 0.12}


@postgres
async def test_a_turn_that_generated_no_token_leaves_its_ttft_out(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    sentence = llm.ChatMessage(role="assistant", content=[""], metrics={})
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=sentence))
    await text.end(session, "caller_hung_up", "caller")
    turn = next(entry for entry in await store.whole(call) if entry.type == "turn.agent")
    assert turn.data["metrics"] == {}


# ── helpers of these tests ──


def _a_slow_cancellation(box: Box, answered: asyncio.Event) -> Session:
    """A spoken call whose model announces a cancellation it runs on an app that waits."""
    assert box.log.call is not None

    async def waiting(use: ToolUse, speech: str | None) -> ToolResult:
        await answered.wait()
        return await box.tool(use, speech)

    platform = Platform(
        append_many=box.log.append_many, tool=waiting, lookup=box.lookup, seal=box.seal
    )
    config = AgentConfig(slug="clinica-norte", tools=(CANCEL,), memory=MemoryPolicy())
    announced = "Le cancelo la reserva del lunes, deme un momento que la busco en el sistema"
    script: JsonObject = {"replies": [[announced, {"name": "cancel", "call_id": "t1"}], ["Listo"]]}
    stages = Pipeline(
        llm=Running(ACME, "k", options=script), stt=Running(ACME, "k"), tts=Running(ACME, "k")
    )
    return voice_session(Call(context_of(box.log.call, "phone"), config, platform), stages)


# A task is ours when any coroutine it is awaiting was written in the package: livekit's task
# running one of our tools counts.
def _ours(task: asyncio.Task[object]) -> bool:
    package = str(Path(pinecall.__file__).parent)
    step: object = task.get_coro()
    while step is not None:
        code = getattr(step, "cr_code", None) or getattr(step, "ag_code", None)
        if code is not None and str(code.co_filename).startswith(package):
            return True
        step = getattr(step, "cr_await", None) or getattr(step, "ag_await", None)
    return False


async def _pieces(*pieces: str) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


async def no_audio() -> AsyncIterator[rtc.AudioFrame]:
    for frame in ():
        yield frame


def speech_event(text: str) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[stt.SpeechData(language=LanguageCode("es"), text=text)],
    )


def a_block(*, cancelled: bool = False) -> metrics.LLMMetrics:
    return metrics.LLMMetrics(
        label="acme", request_id="r1", timestamp=time.time(), duration=0.8, ttft=0.2,
        cancelled=cancelled, completion_tokens=5, prompt_tokens=20, prompt_cached_tokens=0,
        total_tokens=25, tokens_per_second=6.0,
    )  # fmt: skip


def _heard_block(*, streamed: bool) -> metrics.STTMetrics:
    return metrics.STTMetrics(
        label="acme", request_id="r1", timestamp=time.time(), duration=0.0,
        audio_duration=1.0, streamed=streamed,
    )  # fmt: skip


def _a_room(box: Box, call: Call, server: Server) -> CallRoom:
    caller = seat(
        "sip_caller",
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        attributes={CALLER_NUMBER: THE_CALLER},
    )
    assert box.log.call is not None

    async def trunks(_to: str) -> Trunk:
        return Trunk(SIPOutboundConfig(hostname="sip.carrier.test"))

    async def claim(_code: str) -> None:
        return

    async def sent_on(_to: str) -> None:
        return

    legs = Legs(trunk=trunks, sent_on=sent_on)
    return CallRoom(call, AnOfflineRoom(box.log.call, caller), server, legs=legs, claim=claim)
