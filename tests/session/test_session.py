"""One session for a spoken call and a written one: its commands, its people, its tools, its end."""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import get_args

import pytest
from livekit import rtc
from livekit.agents import (
    AgentStateChangedEvent,
    APIStatusError,
    CloseEvent,
    CloseReason,
    ConversationItemAddedEvent,
    RunContext,
    SessionUsageUpdatedEvent,
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
from livekit.agents.metrics import AgentSessionUsage
from livekit.agents.types import NotGiven, TimedString
from livekit.agents.voice import Agent, ModelSettings
from livekit.agents.voice.agent_session import DEFAULT_TTS_TEXT_TRANSFORMS
from livekit.agents.voice.room_io import RoomOptions
from livekit.protocol.sip import SIPOutboundConfig

from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotAvailable
from pinecall.domain.types import (
    AgentConfig,
    Greeting,
    Hangup,
    JsonObject,
    MemoryPolicy,
    PromptBlock,
    ToolSpec,
    Turn,
)
from pinecall.log.store import Store
from pinecall.providers.build import Running
from pinecall.providers.keys import Pipeline
from pinecall.session import session as session_module
from pinecall.session import text
from pinecall.session.call import Call, Platform, ToolUse
from pinecall.session.room import CALLER_NUMBER, Room, Trunk
from pinecall.session.session import (
    A_RELEASE,
    BLOCKS,
    CLOSING,
    MIN_WORDS,
    SAY_GOODBYE_FIRST,
    Session,
    is_a_backchannel,
    keyterms,
    spoken,
)
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
from pinecall.wire.events import CreditsExhausted
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import Supervisor, ToolResult
from pinecall.wire.parts import ToolSpec as WiredTool
from tests.conftest import postgres
from tests.fakes import ACME, Server, seat
from tests.fakes import Room as AnOfflineRoom
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
    assert model_of(session).asked == []


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
    assert len(model_of(session).asked) == 1
    agent = [entry.data["text"] for entry in await store.whole(call) if entry.type == "turn.agent"]
    assert agent == ["Hola, ¿qué necesita?"]


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
    said = [getattr(item, "text_content", "") for item in model_of(session).asked[0].items]
    assert "Su turno es el lunes." in said
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_agent_reply_is_one_model_turn_guided_by_the_instruction(box: Box) -> None:
    session = a_session(box, NOBODY, ["Le confirmo el turno"])
    await session.start()
    await session.apply(AgentReply(instructions="Confirma el turno"))
    await settled()
    asked = model_of(session).asked
    assert len(asked) == 1
    assert "Confirma el turno" in str(
        [getattr(item, "text_content", "") for item in asked[0].items]
    )
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_a_static_block_rewrites_the_instructions_and_the_log_keeps_its_hash(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["ok"])
    await session.start()
    await session.apply(PromptSet(name="identity", text="Sos la recepción de la clínica."))
    await text.hears(session, "hola")
    await text.end(session, "caller_hung_up", "caller")
    first = model_of(session).asked[0].items[0]
    assert "Sos la recepción" in str(getattr(first, "text_content", ""))
    changed = next(entry for entry in await store.whole(call) if entry.type == "prompt.changed")
    assert changed.data["chars"] == len("Sos la recepción de la clínica.")
    assert "recepción" not in str(changed.data)


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
    items = model_of(session).asked[0].items
    assert "Turnos libres" in str(getattr(items[-1], "text_content", ""))
    assert "Turnos libres" not in str(session.agent.instructions)


@postgres
async def test_a_block_the_agent_never_declared_is_refused_and_leaves_no_entry(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(
        box, AgentConfig(slug="clinica-norte", prompt=(PromptBlock("identity", "static"),))
    )
    await session.start()
    with pytest.raises(DeclarationRefused, match="no block named 'view'"):
        await session.apply(PromptSet(name="view", text="x"))
    await text.end(session, "caller_hung_up", "caller")
    assert "prompt.changed" not in await kinds(store, call)


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
async def test_the_caller_is_pinned_before_livekit_links_a_seat_and_a_written_call_hears_none(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[RoomOptions] = []

    class Recorded(RoomOptions):
        def __init__(
            self,
            *,
            participant_identity: str | NotGiven,
            audio_input: bool | NotGiven,
            audio_output: bool | NotGiven,
        ) -> None:
            super().__init__(
                participant_identity=participant_identity,
                audio_input=audio_input,
                audio_output=audio_output,
            )
            told.append(self)

    monkeypatch.setattr(session_module, "RoomOptions", Recorded)
    session = a_session(box, NOBODY)
    await session.start(seat="visitor_1")
    await text.end(session, "caller_hung_up", "caller")
    (options,) = told
    assert options.participant_identity == "visitor_1"
    assert (options.audio_input, options.audio_output) == (False, False)


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
    said = await text.hears(session, "reserva el lunes")
    await text.end(session, "caller_hung_up", "caller")
    assert [(use.name, use.arguments) for use in box.used] == [("book", {"day": "lunes"})]
    assert '"table": "mesa 4"' in _output_of(session, "t1").output
    assert said == "Listo"


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
    history = [getattr(item, "text_content", "") for item in model_of(session).asked[1].items]
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
    first, second = model_of(session).asked
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
        append=box.log.append, tool=answering, lookup=box.lookup, seal=box.seal
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
    asked = next(entry for entry in entries if entry.type == "attention.requested")
    assert asked.data == {"reason": "quiere hablar con un médico", "wait_s": 30.0}
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
    (asked,) = model_of(session).asked
    notes = [str(getattr(item, "text_content", "")) for item in asked.items]
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
    assert model_of(session).asked == []
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
    notes = [str(getattr(item, "text_content", "")) for item in model_of(session).asked[0].items]
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
    said = "quiero cambiar mi turno del lunes"
    session.live.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript=said, is_final=False, language=LanguageCode("es")),
    )
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    heard = [entry for entry in seen if entry.type == "user.transcript"]
    assert (heard[0].data["final"], heard[0].ephemeral) == (False, True)
    assert box.looked == [("recall", {"contact": "+59899123456", "query": said})]


@postgres
async def test_a_turn_too_short_to_start_a_run_starts_none_before_the_turn_ends(box: Box) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", memory=MemoryPolicy()))
    await session.start()
    session.live.emit(
        "user_input_transcribed", UserInputTranscribedEvent(transcript="sí, eso", is_final=False)
    )
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert box.looked == []


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
async def test_a_block_is_written_after_every_listener_saw_it_so_the_speech_id_is_there(
    box: Box,
) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    block = metrics.TTSMetrics(
        label="acme", request_id="r1", timestamp=time.time(), ttfb=0.2, duration=1.0,
        audio_duration=1.0, cancelled=False, characters_count=12, streamed=True,
    )  # fmt: skip
    model_of(session).emit("metrics_collected", block)
    block.speech_id = "speech_9"
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    written = [entry for entry in seen if entry.type == "metrics.tts"]
    assert written[0].data["speech_id"] == "speech_9"


@postgres
async def test_an_agent_turn_carries_its_report_whole_and_whether_it_was_cut_off(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    said = llm.ChatMessage(
        role="assistant", content=["Hasta"], interrupted=True, metrics={"llm_node_ttft": 0.3}
    )
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=said))
    await text.end(session, "caller_hung_up", "caller")
    turn = next(entry for entry in await store.whole(call) if entry.type == "turn.agent")
    assert (turn.data["text"], turn.data["interrupted"]) == ("Hasta", True)
    assert turn.data["metrics"] == {"llm_node_ttft": 0.3}


@postgres
async def test_a_user_turn_carries_the_language_the_recogniser_said_and_its_eou_block(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    session.live.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="hola", is_final=True, language=LanguageCode("es")),
    )
    heard = llm.ChatMessage(
        role="user",
        content=["hola"],
        metrics={"end_of_turn_delay": 0.4, "transcription_delay": 0.1},
    )
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=heard))
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    written = [entry.type for entry in entries]
    assert written.index("metrics.eou") < written.index("turn.user")
    turn = next(entry for entry in entries if entry.type == "turn.user")
    assert turn.data["language"] == "es"


# ── the time a call is given ──


@postgres
async def test_a_limit_under_two_minutes_is_told_at_its_half_and_ends_at_the_limit(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["Vamos cerrando"])
    await session.start()
    await session.keep_time(1, exhausted=None)
    await session.close()
    asked = model_of(session).asked
    assert CLOSING in str([getattr(item, "text_content", "") for item in asked[0].items])
    ended = next(entry for entry in await store.whole(call) if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("timeout", "platform")


@postgres
async def test_a_person_on_the_line_is_not_talked_over_and_the_limit_still_holds(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["nunca"])
    await session.start()
    session.call.taken_by = A_SUPERVISOR
    await session.keep_time(1, exhausted=None)
    await session.close()
    assert model_of(session).asked == []
    assert "call.ended" in await kinds(store, call)


@postgres
async def test_the_orgs_minutes_ending_the_call_first_are_written_before_it_ends(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    spent = CreditsExhausted(org="org_1", quota="minutes", used=30, limit=30)
    await session.keep_time(1, exhausted=spent)
    await session.close()
    written = await kinds(store, call)
    assert written.index("credits.exhausted") < written.index("call.ended")


async def test_no_limit_keeps_no_clock() -> None:
    started = time.monotonic()
    await Session.keep_time(object.__new__(Session), 0, exhausted=None)
    assert time.monotonic() - started < 0.1


# ── a spoken call's session ──


@postgres
async def test_a_spoken_call_is_built_with_the_ears_the_voice_and_the_model_it_asked_for(
    box: Box,
) -> None:
    session = _spoken(box, AgentConfig(slug="clinica-norte"))
    assert [type(one).__name__ for one in session.built] == ["AcmeLLM", "AcmeSTT", "AcmeTTS"]


@postgres
async def test_ears_that_end_the_turn_decide_it_and_others_get_the_local_detector(box: Box) -> None:
    deciding = _spoken(box, NOBODY, ends_the_turn=True)
    local = _spoken(box, NOBODY)
    assert deciding.live.options.turn_handling.get("turn_detection") == "stt"
    assert type(local.live.options.turn_handling.get("turn_detection")).__name__ == "TurnDetector"


@postgres
async def test_what_it_takes_to_cut_the_agent_off_is_the_agents_own_else_two_words(
    box: Box,
) -> None:
    declared = _spoken(box, AgentConfig(slug="clinica-norte", turn=Turn(min_interruption_words=4)))
    inherited = _spoken(box, NOBODY)
    assert declared.live.options.interruption.get("min_words") == 4
    assert inherited.live.options.interruption.get("min_words") == MIN_WORDS == 2


@postgres
async def test_interruptions_are_judged_locally_and_a_cut_sentence_is_never_said_twice(
    box: Box,
) -> None:
    options = _spoken(box, NOBODY).live.options
    assert options.interruption.get("mode") == "vad"
    assert options.interruption.get("resume_false_interruption") is False
    assert options.interruption.get("false_interruption_timeout") == 1.0
    assert options.preemptive_generation.get("enabled") is False


@postgres
async def test_the_tenants_own_pronunciations_are_said_after_livekits_filters(box: Box) -> None:
    said = _spoken(box, AgentConfig(slug="clinica-norte", says={"GSA": "ge ese a"}))
    transforms = list(said.live.options.tts_text_transforms or [])
    assert transforms[: len(DEFAULT_TTS_TEXT_TRANSFORMS)] == list(DEFAULT_TTS_TEXT_TRANSFORMS)
    assert len(transforms) == len(DEFAULT_TTS_TEXT_TRANSFORMS) + 1


def test_agreement_alone_is_a_backchannel_and_anything_that_takes_the_floor_is_not() -> None:
    assert is_a_backchannel("sí, claro")
    assert is_a_backchannel("Mm, ok.")
    assert not is_a_backchannel("sí, pero el martes no puedo")
    assert not is_a_backchannel("")


def test_the_declared_words_come_first_and_the_state_adds_the_names_it_holds() -> None:
    config = AgentConfig(slug="clinica-norte", hears=("Vidal",))
    state: JsonObject = {
        "patient": {"name": "Ana Pérez", "phone": "+59899123456"},
        "note": "x " * 30,
        "who": "Vidal",
    }
    assert keyterms(config, state) == ["Vidal", "Ana Pérez"]


def test_the_keyterms_stop_where_the_vendors_do() -> None:
    state: JsonObject = {f"n{index}": f"Nombre {index}" for index in range(80)}
    assert len(keyterms(NOBODY, state)) == 50


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


def _spoken(
    box: Box, config: AgentConfig, *, ends_the_turn: bool = False, keyterms: bool = False
) -> Session:
    assert box.log.call is not None
    call = Call(context_of(box.log.call, "phone"), config, box.platform())
    ears: JsonObject = {"keyterms": True} if keyterms else {}
    stages = Pipeline(
        llm=Running(ACME, "k"),
        stt=Running(ACME, "k", ends_the_turn=ends_the_turn, options=ears),
        tts=Running(ACME, "k"),
    )
    return spoken(call, stages)


def _component_error(error: Exception, *, recoverable: bool = False) -> object:
    return llm.LLMError(timestamp=time.time(), label="acme", error=error, recoverable=recoverable)


def _output_of(session: Session, call_id: str) -> llm.FunctionCallOutput:
    items = model_of(session).asked[-1].items
    return next(
        item
        for item in items
        if isinstance(item, llm.FunctionCallOutput) and item.call_id == call_id
    )


def test_the_table_names_every_block_the_library_declares() -> None:
    declared = set(get_args(metrics.AgentMetrics))
    assert {livekits for livekits, _, _ in BLOCKS} == declared
    assert {kind for _, kind, _ in BLOCKS} == {
        "metrics.llm",
        "metrics.stt",
        "metrics.tts",
        "metrics.vad",
        "metrics.eou",
        "metrics.eot",
        "metrics.interruption",
        "metrics.realtime",
        "metrics.avatar",
    }


@postgres
async def test_the_vad_is_livekits_own_local_one_built_when_none_is_given(box: Box) -> None:
    spoken_call = _spoken(box, NOBODY)
    assert spoken_call.live.vad is not None
    assert type(spoken_call.live.vad).__module__.startswith("livekit.agents.inference")


@postgres
async def test_a_written_call_hears_nothing_speaks_nothing_and_takes_its_turns_by_hand(
    box: Box,
) -> None:
    session = a_session(box, NOBODY)
    assert [type(one).__name__ for one in session.built] == ["AcmeLLM"]
    assert session.live.options.turn_handling.get("turn_detection") == "manual"


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
    said = [entry.data for entry in seen if entry.type == "agent.transcript"]
    assert [(one["text"], one.get("start"), one.get("end")) for one in said] == [
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
    (said,) = [entry.data for entry in seen if entry.type == "agent.transcript"]
    assert "start" not in said
    assert "end" not in said


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
    assert box.looked == []


@postgres
async def test_an_agent_reply_is_no_query_and_asks_nobody(box: Box) -> None:
    session = a_session(box, AgentConfig(slug="clinica-norte", memory=MemoryPolicy()), ["ok"])
    await session.start()
    await session.apply(AgentReply(instructions="Ofrecé el martes a las diez de la mañana"))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert box.looked == []


@postgres
async def test_a_backchannel_over_the_agents_voice_never_reaches_the_model(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    heard = [_speech("sí, claro"), _speech("sí, pero el martes no puedo")]

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
    over_the_agent = [event async for event in session.agent.stt_node(_no_audio(), ModelSettings())]
    session.live.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="speaking", new_state="listening")
    )
    after_it = [event async for event in session.agent.stt_node(_no_audio(), ModelSettings())]
    await text.end(session, "caller_hung_up", "caller")
    assert [event.alternatives[0].text for event in over_the_agent] == [
        "sí, pero el martes no puedo"
    ]
    assert len(after_it) == 2


# ── the ears' words ──


@postgres
async def test_the_ears_are_told_the_names_the_state_is_holding_the_moment_it_moves(
    box: Box,
) -> None:
    session = _spoken(box, AgentConfig(slug="clinica-norte", hears=("Vidal",)), keyterms=True)
    session.call.writing.open()
    await session.apply(StateSet(state={"patient": {"name": "Ana Pérez"}}))
    await session.call.writing.close(5)
    assert session.live.keyterms == ["Vidal", "Ana Pérez"]


@postgres
async def test_ears_with_no_keyterms_door_are_never_told_anything(box: Box) -> None:
    session = _spoken(box, AgentConfig(slug="clinica-norte", hears=("Vidal",)))
    session.call.writing.open()
    await session.apply(StateSet(state={"patient": {"name": "Ana Pérez"}}))
    await session.call.writing.close(5)
    assert session.live.keyterms == []
    assert session.live.options.stt_context_options.get("keyterms") == []


@postgres
async def test_the_words_the_agent_declared_it_hears_reach_the_ears_that_take_them(
    box: Box,
) -> None:
    session = _spoken(box, AgentConfig(slug="clinica-norte", hears=("Vidal", "GSA")), keyterms=True)
    assert session.live.options.stt_context_options.get("keyterms") == ["Vidal", "GSA"]
    unsaid = _spoken(box, NOBODY, keyterms=True)
    assert unsaid.live.options.stt_context_options.get("keyterms") == []


def test_an_agent_that_declared_nothing_and_holds_nothing_asks_for_nothing() -> None:
    assert keyterms(NOBODY, {}) == []


@postgres
async def test_a_spoken_call_asks_the_voice_to_align_the_transcript_it_speaks(box: Box) -> None:
    assert _spoken(box, NOBODY).live.options.use_tts_aligned_transcript is True


# ── the line ──


@postgres
async def test_a_held_call_leaves_the_agent_neither_speaking_nor_hearing(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallHold())
    assert (session.live.input.audio_enabled, session.live.output.audio_enabled) == (False, False)
    await text.end(session, "caller_hung_up", "caller")


@postgres
async def test_taking_the_call_off_hold_gives_the_agent_its_ears_before_its_voice(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(CallHold())
    switched: list[tuple[str, bool]] = []

    def ears(on: object) -> None:
        switched.append(("ears", on is True))

    def voice(on: object) -> None:
        switched.append(("voice", on is True))

    monkeypatch.setattr(session.live.input, "set_audio_enabled", ears)
    monkeypatch.setattr(session.live.output, "set_audio_enabled", voice)
    await session.apply(CallUnhold())
    await text.end(session, "caller_hung_up", "caller")
    assert switched == [("ears", True), ("voice", True)]


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
async def test_a_takeover_with_nothing_to_interrupt_still_takes_the_line(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    await session.apply(supervised(TakeoverVerb()))
    assert session.call.taken_by == A_SUPERVISOR
    await text.end(session, "caller_hung_up", "caller")


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
    assert not session.held


@postgres
async def test_a_release_gives_the_ears_back_first_and_then_the_voice(
    box: Box, store: Store, call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = a_session(box, NOBODY, ["¿Seguimos?"])
    await session.start()
    await session.apply(supervised(TakeoverVerb()))
    switched: list[str] = []

    def ears(_on: object) -> None:
        switched.append("ears")

    def voice(_on: object) -> None:
        switched.append("voice")

    monkeypatch.setattr(session.live.input, "set_audio_enabled", ears)
    monkeypatch.setattr(session.live.output, "set_audio_enabled", voice)
    await session.apply(supervised(ReleaseVerb()))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    assert switched[:2] == ["ears", "voice"]
    assert "supervisor.released" in await kinds(store, call)


@postgres
async def test_every_one_of_the_six_verbs_lands_in_the_callers_log(
    box: Box, store: Store, call: str, server: Server
) -> None:
    session = _spoken(box, NOBODY)
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
    asked = next(entry for entry in entries if entry.type == "supervisor.transferred")
    assert asked.data["mode"] == "cold"
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
    assert len(model_of(session).asked) == 2


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
        append=box.log.append, tool=refusing, lookup=box.lookup, seal=box.seal
    )
    await session.start()
    said = await text.hears(session, "reserva")
    await text.end(session, "caller_hung_up", "caller")
    output = _output_of(session, "t1")
    assert (output.output, output.is_error) == ("the app is not connected", True)
    assert said == "Probemos luego"


@postgres
async def test_the_tool_runs_after_its_announcement_with_or_without_a_receipt(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    happened: list[str] = []

    async def played(_context: RunContext[None]) -> None:
        happened.append("played")

    monkeypatch.setattr(RunContext, "wait_for_playout", played)
    answering = box.tool

    async def asked(use: ToolUse, speech: str | None) -> ToolResult:
        happened.append(use.name)
        return await answering(use, speech)

    session = a_session(
        box,
        BOOKING,
        [{"name": "book", "arguments": {}, "call_id": "t1"}, {"name": "cancel", "call_id": "t2"}],
        ["hecho"],
    )
    session.call.platform = Platform(
        append=box.log.append, tool=asked, lookup=box.lookup, seal=box.seal
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
    model_of(session).emit("metrics_collected", _a_block())
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
    assert len(model_of(session).asked) == 1


@postgres
async def test_the_agent_is_told_a_minute_before_a_limit_of_two_minutes_or_more(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    real = asyncio.sleep

    async def counted(delay: float) -> None:
        slept.append(delay)
        await real(0)

    session = a_session(box, NOBODY, ["cerramos"])
    await session.start()
    monkeypatch.setattr(session_module.asyncio, "sleep", counted)
    await session.keep_time(300, exhausted=None)
    monkeypatch.undo()
    await session.close()
    assert slept[:2] == [240, 60]


# ── metrics ──


@postgres
async def test_every_field_of_a_block_reaches_the_log_under_its_own_name(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    block = _a_block()
    model_of(session).emit("metrics_collected", block)
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    (written,) = [entry.data for entry in seen if entry.type == "metrics.llm"]
    for name, value in block.model_dump(mode="json").items():
        assert written[name] == value


@postgres
async def test_a_cancelled_block_from_a_discarded_generation_is_logged(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    model_of(session).emit("metrics_collected", _a_block(cancelled=True))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    (written,) = [entry.data for entry in seen if entry.type == "metrics.llm"]
    assert written["cancelled"] is True


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
    said = llm.ChatMessage(role="assistant", content=[""], metrics={})
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=said))
    await text.end(session, "caller_hung_up", "caller")
    turn = next(entry for entry in await store.whole(call) if entry.type == "turn.agent")
    assert turn.data["metrics"] == {}


# ── helpers of these tests ──


async def _pieces(*pieces: str) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


async def _no_audio() -> AsyncIterator[rtc.AudioFrame]:
    for frame in ():
        yield frame


def _speech(said: str) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[stt.SpeechData(language=LanguageCode("es"), text=said)],
    )


def _a_block(*, cancelled: bool = False) -> metrics.LLMMetrics:
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


def _a_room(box: Box, call: Call, server: Server) -> Room:
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

    return Room(call, AnOfflineRoom(box.log.call, caller), server, trunks=trunks, claim=claim)


@postgres
async def test_a_row_livekit_grew_a_field_for_is_still_the_calls_usage(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    heard = metrics.STTModelUsage(provider="acme", model="acme-ears", audio_duration=3.0)
    session.live.emit(
        "session_usage_updated",
        SessionUsageUpdatedEvent(usage=AgentSessionUsage(model_usage=[heard])),
    )
    await text.end(session, "caller_hung_up", "caller")
    ((usage, _),) = box.sealed
    assert [(row.type, row.model) for row in usage] == [("stt_usage", "acme-ears")]
