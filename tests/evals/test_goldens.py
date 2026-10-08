"""Tests for a golden played on a written call, and the judges its expectations set."""

import asyncio
import time

import pytest

from pinecall.domain.agent import AgentConfig, Greeting
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Json, JsonObject
from pinecall.evals import goldens
from pinecall.evals.case import Case, case_of
from pinecall.evals.goldens import (
    A_GOLDEN,
    drive,
    events_after,
    golden_judges,
    golden_lookup,
    settled,
)
from pinecall.evals.judges import hangup_judges
from pinecall.log.logs import Fanout, Log
from pinecall.log.store import Store
from pinecall.wire.frames import Entry
from pinecall.wire.parts import PlatformTool
from pinecall.wire.rest.evals import Golden
from tests.conftest import postgres
from tests.evals.conftest import (
    THE_CLINIC,
    WHEN,
    a_judge,
    a_log,
    agent,
    agent_line,
    arrived,
    caller,
    caller_line,
    case_of_turns,
    expecting,
    judge_named,
    logged_call,
    score_of,
    verdict,
    with_no_gate,
)
from tests.session.conftest import AGENT, Box, a_session

A_FACT = "Prefiere que le llamen por la mañana"


A_TICK = Entry(seq=1, ts=1.0, call="c", agent=AGENT, type="user.state", ephemeral=True, data={})


FREED = AgentConfig(slug=AGENT, events={"slot_freed": frozenset({"app"})})


class Index:
    """The real index as a golden's lookup reaches it: what it was asked, and nothing found."""

    def __init__(self) -> None:
        """Asked nothing yet."""
        self.asked_for: list[PlatformTool] = []

    async def lookup(
        self, tool: PlatformTool, _arguments: JsonObject, _speech: str | None
    ) -> JsonObject:
        """Nothing found, and the tool kept."""
        self.asked_for.append(tool)
        return {"chunks": []}


def a_golden(**written: object) -> Golden:
    return Golden.model_validate({"name": "reserva", "input": ["hola"], **written})


def test_the_facts_injected_after_a_turn_come_in_the_order_the_golden_lists_them() -> None:
    golden = a_golden(
        events=[
            {"after_turn": 1, "name": "b"},
            {"name": "a"},
            {"after_turn": 1, "name": "c"},
        ]
    )
    assert [event.name for event in events_after(golden, 1)] == ["b", "c"]
    assert [event.name for event in events_after(golden, 0)] == ["a"]


async def test_a_recall_is_answered_with_the_goldens_own_facts() -> None:
    index = Index()
    found = await golden_lookup([A_FACT], index.lookup)("recall", {}, None)
    assert found == {"facts": [{"text": A_FACT, "source": A_GOLDEN}]}


async def test_the_gateway_is_never_asked_to_recall_for_a_golden() -> None:
    index = Index()
    await golden_lookup([A_FACT], index.lookup)("recall", {}, None)
    assert index.asked_for == []


async def test_a_search_is_the_real_index_because_that_is_what_a_golden_is_asking() -> None:
    index = Index()
    found = await golden_lookup([A_FACT], index.lookup)("search", {"query": "x"}, None)
    assert found == {"chunks": []}
    assert index.asked_for == ["search"]


def test_a_golden_that_seeds_nothing_reads_as_seeding_nothing() -> None:
    assert a_golden().memory == []


async def test_a_log_that_stays_quiet_is_settled_at_once_and_a_busy_one_at_most_in_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fanout = Fanout()
    quiet = fanout.subscribe()
    began = time.monotonic()
    await settled(quiet, quiet_s=0.01)
    assert time.monotonic() - began < 0.5
    monkeypatch.setattr(goldens, "AT_MOST_S", 0.05)
    busy = fanout.subscribe()

    async def chatter() -> None:
        while True:
            fanout.publish(A_TICK)
            await asyncio.sleep(0.001)

    talking = asyncio.create_task(chatter())
    began = time.monotonic()
    await settled(busy, quiet_s=0.02)
    talking.cancel()
    assert time.monotonic() - began < 0.5


async def test_a_log_that_ended_is_settled() -> None:
    fanout = Fanout()
    ended = fanout.subscribe()
    fanout.close()
    await settled(ended)


async def played(
    store: Store, call: str, golden: Golden, config: AgentConfig, *replies: list[Json]
) -> tuple[goldens.Played, list[str]]:
    box = Box(Log(store, call, AGENT))
    session = a_session(box, config, *replies, run="run_1")
    heard = box.log.fanout.subscribe()
    result = await drive(session, golden, heard, is_held=lambda: True)
    return result, [entry.type for entry in await store.whole(call)]


@postgres
async def test_a_golden_run_writes_no_opening_turn_at_all(
    store: Store, call: str, acme: str
) -> None:
    del acme
    greets = AgentConfig(slug=AGENT, greeting=Greeting(say="Clínica Norte, buenos días."))
    _, kinds = await played(store, call, a_golden(), greets, ["hola"])
    assert kinds.index("turn.user") < kinds.index("turn.agent")


# The state rides call.started to the app, which applies it: the runner declares none of its own.
@postgres
async def test_a_golden_with_a_state_is_played_without_the_runner_declaring_one(
    store: Store, call: str, acme: str
) -> None:
    del acme
    golden = a_golden(state={"stage": "book", "patient": {"id": "p-1"}})
    _, kinds = await played(store, call, golden, FREED, ["hola"])
    assert "state.changed" not in kinds


@postgres
async def test_a_golden_with_an_event_injects_it_at_the_declared_turn(
    store: Store, call: str, acme: str
) -> None:
    del acme
    golden = a_golden(
        input=["hola", "¿hay algo antes?"],
        events=[{"after_turn": 1, "name": "slot_freed", "data": {"at": "10:15"}}],
    )
    result, kinds = await played(
        store, call, golden, FREED, ["Buenos días."], ["Se liberó a las 10:15."]
    )
    callers = [index for index, kind in enumerate(kinds) if kind == "turn.user"]
    answers = [index for index, kind in enumerate(kinds) if kind == "turn.agent"]
    assert answers[0] < kinds.index("event.received") < callers[1]
    assert result.held
    assert len(result.requests) == 2


@postgres
async def test_a_golden_whose_event_the_agent_never_declared_is_refused_by_name_and_ended(
    store: Store, call: str, acme: str
) -> None:
    del acme
    golden = a_golden(events=[{"name": "meteorite"}])
    with pytest.raises(DeclarationRefused, match="meteorite"):
        await played(store, call, golden, FREED, ["hola"])
    ended = [entry for entry in await store.whole(call) if entry.type == "call.ended"]
    assert [entry.data["reason"] for entry in ended] == ["error"]


@postgres
async def test_a_golden_whose_app_let_go_ends_as_app_detached_and_not_on_a_timeout(
    store: Store, call: str, acme: str
) -> None:
    del acme
    box = Box(Log(store, call, AGENT))
    session = a_session(box, FREED, ["hola"], run="run_1")
    result = await drive(session, a_golden(), box.log.fanout.subscribe(), is_held=lambda: False)
    assert not result.held
    ended = [entry for entry in await store.whole(call) if entry.type == "call.ended"]
    assert [(entry.data["reason"], entry.data["ended_by"]) for entry in ended] == [
        ("app_detached", "platform")
    ]


@postgres
async def test_a_golden_played_to_its_end_is_a_caller_who_hung_up(
    store: Store, call: str, acme: str
) -> None:
    del acme
    _, kinds = await played(store, call, a_golden(), FREED, ["hola"])
    entries = await store.whole(call)
    assert kinds[-1] == "call.score"
    ended = next(entry for entry in entries if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("caller_hung_up", "caller")


# ── what a golden expects ──


def test_consent_leads_and_the_rest_come_in_the_order_expect_names_them() -> None:
    case = case_of_turns(caller_line("hola"))
    golden = expecting(
        says=["45"], says_any=["sí"], **{"not": ["gratis"]}, tools=["find"], register="usted"
    )
    assert [judge.name for judge in golden_judges(golden, case)] == [
        "consent",
        "heard",
        "tools",
        "silence",
        "says",
        "says_any",
        "register",
    ]


async def test_a_hang_up_judge_a_golden_names_is_asked_of_its_call_from_the_panel() -> None:
    model = a_judge(("fail", "it promised a call back nobody booked"))
    case = case_of_turns(caller_line("hola"), agent_line("Le llamaremos mañana."))
    panel = hangup_judges(case, [])
    golden = expecting(judges=["promises", "consent"])
    judges = golden_judges(golden, case, panel)
    assert [judge.name for judge in judges] == ["consent", "heard", "promises"], "consent once"
    assert await score_of(judge_named(judges, "promises"), case, model) == 0.0
    assert len(model.requests) == 1


async def test_a_judge_the_panel_does_not_hold_breaks_the_golden_and_names_the_panel() -> None:
    case = case_of_turns(caller_line("hola"))
    golden = expecting(judges=["persona"])
    result = await verdict(judge_named(golden_judges(golden, case, []), "persona"), case)
    assert result.failed
    assert "asks persona, and the panel this call meets has no such judge" in result.reasoning


def test_a_golden_with_no_line_for_the_caller_is_not_asked_whether_it_was_heard() -> None:
    golden = Golden.model_validate({"name": "g"})
    assert [judge.name for judge in golden_judges(golden, case_of_turns())] == ["consent"]


async def test_a_caller_the_agent_never_heard_breaks_the_golden_whatever_else_held() -> None:
    golden = Golden.model_validate({"name": "g", "input": ["hola", "adiós"]})
    case = case_of_turns(caller_line("hola"))
    result = await verdict(judge_named(golden_judges(golden, case), "heard"), case)
    assert result.failed
    assert "the golden says 2 lines and the agent heard 1" in result.reasoning


async def test_a_conversation_that_called_every_named_tool_holds_without_a_judge() -> None:
    model = a_judge()
    case = case_of_turns(
        caller_line(WHEN),
        agent_line("Le busco un hueco.", calls=(logged_call("find_slots", {}, "[]"),)),
    )
    judge = judge_named(golden_judges(expecting(tools=["find_slots"]), case), "tools")
    assert await score_of(judge, case, model) == 1.0
    assert model.requests == []


async def test_a_tool_the_golden_named_and_nobody_called_is_named_in_the_reason() -> None:
    case = case_of_turns(
        caller_line(WHEN),
        agent_line("Le busco un hueco.", calls=(logged_call("find_slots", {}, "[]"),)),
    )
    result = await verdict(
        judge_named(golden_judges(expecting(tools=["book"]), case), "tools"), case
    )
    assert result.failed
    assert "book" in result.reasoning
    assert "find_slots" in result.reasoning


async def test_a_conversation_that_called_nothing_says_so_rather_than_listing_nothing() -> None:
    case = case_of_turns(caller_line(WHEN), agent_line("No sé."))
    result = await verdict(
        judge_named(golden_judges(expecting(tools=["find"]), case), "tools"), case
    )
    assert "no tool at all" in result.reasoning


# Consent holds on this log (no gate is built), so only not_tools catches the booking.
async def test_a_forbidden_tool_that_ran_is_caught_with_the_seq_the_log_gave_it() -> None:
    case = case_of(with_no_gate(), THE_CLINIC)
    judges = golden_judges(expecting(not_tools=["book_appointment"]), case)
    assert (await verdict(judge_named(judges, "consent"), case)).passed
    result = await verdict(judge_named(judges, "not_tools"), case)
    assert result.reasoning == (
        "the golden forbids book_appointment, and this call ran book_appointment at seq 3"
    )


async def test_a_call_that_kept_off_the_forbidden_tool_holds_and_asks_nobody() -> None:
    case = case_of(with_no_gate(), THE_CLINIC)
    judge = judge_named(golden_judges(expecting(not_tools=["transfer"]), case), "not_tools")
    result = await verdict(judge, case)
    assert result.passed
    assert "none of the 1 forbidden tool(s) ran" in result.reasoning


async def test_the_phrases_hold_on_the_very_call_the_forbidden_tool_judge_breaks() -> None:
    log = a_log(
        caller("Me viene bien la de las cuatro."),
        ("tool.call", {"call_id": "c1", "name": "book", "arguments": {}, "speech_id": "sp_9"}),
        agent("Voy a reservarle la cita. Le llega un SMS con la confirmación."),
    )
    case = case_of(log, None)
    banned = ["queda reservada", "he reservado", "está reservada"]
    judges = golden_judges(expecting(**{"not": banned}, not_tools=["book"]), case)
    assert (await verdict(judge_named(judges, "silence"), case)).passed
    assert not (await verdict(judge_named(judges, "not_tools"), case)).passed


async def test_a_phrase_is_found_in_whichever_turn_the_agent_put_it_in() -> None:
    case = case_of_turns(caller_line("hola"), agent_line("Un momento."), agent_line("A las 10:15."))
    judge = judge_named(golden_judges(expecting(says=["10:15"]), case), "says")
    assert (await verdict(judge, case)).passed


async def test_a_phrase_is_matched_the_way_a_person_reads_it_and_not_by_case() -> None:
    case = case_of_turns(caller_line("hola"), agent_line("La consulta cuesta 45 Euros."))
    judge = judge_named(golden_judges(expecting(says=["45 euros"]), case), "says")
    assert (await verdict(judge, case)).passed


async def test_a_phrase_the_agent_never_said_names_itself_in_the_reason() -> None:
    case = case_of_turns(caller_line("hola"), agent_line("Cuesta 45 euros."))
    result = await verdict(
        judge_named(golden_judges(expecting(says=["gratis"]), case), "says"), case
    )
    assert result.failed
    assert "'gratis'" in result.reasoning


async def test_an_expectation_with_several_right_answers_holds_on_any_one_of_them() -> None:
    case = case_of_turns(caller_line("¿Abren el sábado?"), agent_line("Sí, de nueve a DOS."))
    accepted = ["de 9 a 14", "de nueve a dos", "por la mañana"]
    judge = judge_named(golden_judges(expecting(says_any=accepted), case), "says_any")
    result = await verdict(judge, case)
    assert result.passed
    assert "'de nueve a dos'" in result.reasoning


async def test_an_expectation_none_of_whose_answers_was_said_names_every_one() -> None:
    case = case_of_turns(caller_line("¿Abren el sábado?"), agent_line("No lo sé."))
    judges = golden_judges(expecting(says_any=["de 9 a 14", "por la mañana"]), case)
    result = await verdict(judge_named(judges, "says_any"), case)
    assert result.failed
    assert "'de 9 a 14', 'por la mañana'" in result.reasoning


async def test_a_forbidden_phrase_is_caught_and_the_turn_that_said_it_is_named() -> None:
    case = case_of_turns(
        caller_line("hola"), agent_line("Un momento."), agent_line("La primera es gratis.")
    )
    judge = judge_named(golden_judges(expecting(**{"not": ["gratis"]}), case), "silence")
    assert "'gratis' in agent turn 2" in (await verdict(judge, case)).reasoning


async def test_silence_holds_when_none_of_the_forbidden_phrases_was_said() -> None:
    case = case_of_turns(caller_line("hola"), agent_line("Cuesta 45 euros."))
    judge = judge_named(golden_judges(expecting(**{"not": ["gratis"]}), case), "silence")
    assert (await verdict(judge, case)).passed


def an_event(name: str, data: JsonObject) -> Case:
    return case_of_turns(
        caller_line("hola"),
        agent_line("Un momento.", seq=2),
        caller_line("¿Y ahora?", seq=4),
        agent_line("Se liberó el de las 10:15.", seq=5),
        arrivals=(arrived(name, data, seq=3),),
    )


async def took_it_up(case: Case, *, replies: bool) -> bool:
    judge = judge_named(golden_judges(expecting(replies=replies), case), "replies")
    return (await verdict(judge, case)).passed


async def test_an_agent_that_names_what_the_event_carried_has_taken_it_up() -> None:
    assert await took_it_up(an_event("slot_freed", {"at": "10:15"}), replies=True)


# Whether the agent mentions it, never when: the timing is the app's.
async def test_the_reply_that_followed_is_the_agent_turn_after_it_whenever_it_came() -> None:
    assert not await took_it_up(an_event("slot_freed", {"at": "10:15"}), replies=False)


async def test_an_agent_that_says_nothing_the_event_carried_has_left_it_alone() -> None:
    case = an_event("promo_started", {"code": "VERANO"})
    assert await took_it_up(case, replies=False)
    assert not await took_it_up(case, replies=True)


async def test_a_golden_that_expects_a_reply_to_an_event_that_never_arrived_is_broken() -> None:
    case = case_of_turns(caller_line("hola"), agent_line("Un momento."))
    judge = judge_named(golden_judges(expecting(replies=True), case), "replies")
    assert "no event.received reached the call" in (await verdict(judge, case)).reasoning


# ── the register ──


async def register_holds(reply: str, expected: str) -> bool:
    case = case_of_turns(caller_line("Hola, quería una cita."), agent_line(reply))
    judge = judge_named(golden_judges(expecting(register=expected), case), "register")
    return (await verdict(judge, case)).passed


async def test_an_agent_asked_for_usted_that_keeps_it_holds_without_a_judge() -> None:
    assert await register_holds("Claro, usted dirá. ¿Su nombre?", "usted")


async def test_an_agent_asked_for_usted_that_tutea_is_caught_word_by_word() -> None:
    case = case_of_turns(caller_line("Hola"), agent_line("Vale, dime tu nombre."))
    result = await verdict(
        judge_named(golden_judges(expecting(register="usted"), case), "register"), case
    )
    assert "'tu' in agent turn 1" in result.reasoning
    assert "asked for usted" in result.reasoning


async def test_an_agent_asked_for_tu_that_slips_into_usted_is_caught_too() -> None:
    assert not await register_holds("Cuando usted quiera.", "tu")


async def test_a_turn_that_marks_neither_register_is_not_a_slip() -> None:
    assert await register_holds("Un momento, lo compruebo.", "usted")


async def test_a_word_that_only_contains_a_marker_is_not_the_marker() -> None:
    assert await register_holds("Le paso con el tutor del paciente.", "usted")


async def test_the_ambiguous_third_person_words_are_left_out_on_purpose() -> None:
    assert await register_holds("Le confirmo su cita.", "tu")
