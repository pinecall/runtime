"""The reducer's rules, one sentence each, on logs small enough to read."""

from typing import Any

import pytest

from pinecall.log.reduce import apply, initial_state, reduce
from pinecall_protocol import decode_entry, encode
from pinecall_protocol.envelope import Entry

pytestmark = pytest.mark.unit

CALLER = {"id": "ct_1", "phone": "+34600000001", "name": "Ana"}
LINE = {"channel": "phone", "from": "+34600000001", "to": "+34910000001"}
ROOM = {"name": "call-CA_1", "sid": "RM_1", "channel": "phone"}
SEAT = {
    "identity": "sip_+34600000001",
    "kind": "caller",
    "attributes": {"sip.phoneNumber": "+34600000001"},
}


def entry(seq: int, type_: str, data: dict[str, Any], ephemeral: bool = False) -> Entry:
    return decode_entry(
        {
            "seq": seq,
            "ts": 1786537500.0 + seq,
            "call": "CA_1",
            "agent": "clinica-norte",
            "type": type_,
            "ephemeral": ephemeral,
            "data": data,
        }
    )


def test_an_empty_log_is_idle_with_nothing_known() -> None:
    state = reduce([])
    assert state.seq == 0 and state.status == "idle" and state.call is None
    assert state.turns == [] and state.tools == [] and state.app_state == {}


def test_a_ringing_call_knows_who_is_calling_before_media_is_up() -> None:
    route = {"channel": "phone", "number": "+34910000001"}
    state = reduce([entry(1, "call.ringing", {**LINE, "route": route, "caller": CALLER})])
    assert state.status == "ringing" and state.direction == "inbound"
    assert state.caller is not None and state.caller.name == "Ana"
    assert state.started_at is None and state.seq == 1 and state.call == "CA_1"


def test_a_finished_turn_clears_the_words_on_screen() -> None:
    state = reduce(
        [
            entry(1, "user.transcript", {"text": "hola qu", "final": False}, ephemeral=True),
            entry(2, "turn.user", {"speech_id": "s1", "text": "Hola, quería cita.", "metrics": {}}),
        ]
    )
    assert state.live.user is None
    assert len(state.turns) == 1 and state.turns[0].text == "Hola, quería cita."


def test_the_agents_words_on_screen_are_its_deltas_joined() -> None:
    def word(seq: int, text: str, start: float) -> Entry:
        said = {"speech_id": "s1", "text": text, "final": False, "start": start, "end": start + 0.2}
        return entry(seq, "agent.transcript", said, ephemeral=True)

    def token(seq: int, text: str) -> Entry:
        said = {"speech_id": "s2", "text": text, "final": False}
        return entry(seq, "agent.transcript", said, ephemeral=True)

    spoken = reduce([word(1, "Buenos", 0), word(2, "días,", 0.3), word(3, "Clínica", 0.6)])
    assert spoken.live.agent == "Buenos días, Clínica"
    written = reduce(
        [token(1, "Buenos"), token(2, " días"), token(3, ","), token(4, " clean"), token(5, "ing")]
    )
    assert written.live.agent == "Buenos días, cleaning"
    turn: dict[str, object] = {
        "speech_id": "s1",
        "text": "Buenos días.",
        "interrupted": False,
        "metrics": {},
    }
    closed = reduce([word(1, "Buenos", 0), entry(2, "turn.agent", turn)])
    assert closed.live.agent is None


def test_a_tool_result_closes_its_call_as_done_or_failed() -> None:
    call = {"call_id": "t1", "name": "find_slots", "arguments": {"day": "jueves"}}
    done = reduce(
        [
            entry(1, "tool.call", call),
            entry(2, "tool.result", {"call_id": "t1", "name": "find_slots", "output": [1]}),
        ]
    )
    failed = reduce(
        [
            entry(1, "tool.call", call),
            entry(2, "tool.result", {"call_id": "t1", "name": "find_slots", "error": "down"}),
        ]
    )
    assert done.tools[0].status == "done" and done.tools[0].output == [1] and done.tools[0].seq == 1
    assert failed.tools[0].status == "failed" and failed.tools[0].error == "down"


def test_a_granted_confirm_settles_the_pending_request_with_what_was_said() -> None:
    asked: dict[str, Any] = {
        "tool": "book",
        "call_id": "t2",
        "arguments": {},
        "audience": "sha256:x",
        "phrase": "¿Confirmo?",
        "ttl_s": 120,
    }
    state = reduce(
        [
            entry(1, "confirm.request", asked),
            entry(
                2,
                "confirm.granted",
                {
                    "tool": "book",
                    "call_id": "t2",
                    "audience": "sha256:x",
                    "said": "sí",
                    "ttl_s": 120,
                },
            ),
        ]
    )
    assert state.confirms[0].status == "granted" and state.confirms[0].said == "sí"


def test_a_gap_with_a_snapshot_replaces_everything_and_is_remembered() -> None:
    snapshot = encode(
        reduce(
            [
                entry(
                    1,
                    "call.started",
                    {**LINE, "direction": "inbound", "caller": CALLER, "started_at": 1.0},
                )
            ]
        )
    )
    state = reduce(
        [entry(3, "log.gap", {"from_seq": 1, "to_seq": 3, "snapshot": snapshot}, ephemeral=True)]
    )
    assert state.status == "active" and state.seq == 3
    assert [(gap.from_seq, gap.to_seq) for gap in state.gaps] == [(1, 3)]


def test_a_gap_without_a_snapshot_only_moves_the_cursor() -> None:
    state = apply(
        initial_state(),
        entry(5, "log.gap", {"from_seq": 4, "to_seq": 5, "snapshot": None}, ephemeral=True),
    )
    assert state.status == "idle" and state.seq == 5 and len(state.gaps) == 1


def test_metric_blocks_are_kept_by_kind_in_the_order_they_came() -> None:
    eou = {
        "type": "eou_metrics",
        "timestamp": 1.0,
        "end_of_utterance_delay": 0.4,
        "transcription_delay": 0.1,
        "on_user_turn_completed_delay": 0.0,
        "speech_id": "s1",
    }
    state = reduce(
        [entry(1, "metrics.eou", eou), entry(2, "metrics.eou", {**eou, "speech_id": "s2"})]
    )
    assert [block.speech_id for block in state.metrics.eou] == ["s1", "s2"]
    assert state.metrics.llm == []


def test_a_supervisor_taking_over_and_releasing_leaves_the_line_with_the_agent() -> None:
    who = {"id": "sup_1", "name": "Lucía"}
    state = reduce([entry(1, "supervisor.took_over", {"by": who})])
    assert state.handoff.active is True and state.handoff.by is not None
    state = apply(state, entry(2, "supervisor.released", {"by": who}))
    assert state.handoff.active is False and state.handoff.by is None


def test_the_summary_brings_the_bill_and_the_outcome() -> None:
    usage = [
        {"type": "tts_usage", "provider": "elevenlabs", "model": "flash", "characters_count": 40}
    ]
    cost: dict[str, Any] = {
        "eur": 0.001,
        "rate": {"currency": "EUR", "usd_to_eur": 0.92, "as_of": "2026-08-12"},
        "rows": [],
        "unpriced": [],
    }
    state = reduce(
        [
            entry(
                1,
                "call.summary",
                {
                    "reason": "caller_hung_up",
                    "outcome": "booked",
                    "duration_s": 10.0,
                    "turns": 2,
                    "usage": usage,
                    "cost": cost,
                },
            )
        ]
    )
    assert state.outcome == "booked" and state.end_reason == "caller_hung_up"
    assert state.cost is not None and state.cost.eur == 0.001 and state.usage[0].type == "tts_usage"


def test_a_room_fills_as_participants_join_and_the_caller_takes_the_caller_seat() -> None:
    state = reduce([entry(1, "room.opened", ROOM), entry(2, "participant.joined", SEAT)])
    assert state.room is not None and state.room.caller == "sip_+34600000001"
    who = state.room.participants[0]
    assert who.joined_at == 1786537500.0 + 2 and who.speaking is False and who.name is None
    assert who.attributes == {"sip.phoneNumber": "+34600000001"}


def test_the_room_lights_a_participant_while_it_hears_them() -> None:
    lit = {"identity": "sip_+34600000001", "speaking": True}
    state = reduce(
        [
            entry(1, "room.opened", ROOM),
            entry(2, "participant.joined", SEAT),
            entry(3, "participant.speaking", lit, ephemeral=True),
        ]
    )
    assert state.room is not None and state.room.participants[0].speaking is True
    state = apply(
        state, entry(4, "participant.speaking", {**lit, "speaking": False}, ephemeral=True)
    )
    assert state.room is not None and state.room.participants[0].speaking is False


def test_a_participant_leaving_is_forgotten_and_the_caller_seat_empties() -> None:
    gone = {"identity": "sip_+34600000001", "reason": "client_initiated"}
    state = reduce(
        [
            entry(1, "room.opened", ROOM),
            entry(2, "participant.joined", SEAT),
            entry(3, "participant.left", gone),
        ]
    )
    assert state.room is not None and state.room.participants == [] and state.room.caller is None


def test_a_participant_fact_before_the_room_opened_changes_nothing() -> None:
    state = reduce([entry(1, "participant.joined", SEAT)])
    assert state.room is None


def test_an_outside_fact_is_kept_by_name_and_origin_and_its_cause_names_it() -> None:
    fact = {"name": "slot.released", "data": {"at": "10:15"}, "source": "app"}
    moved = {"state": {"slots": ["10:15"]}, "changed": ["slots"]}
    cause = {"kind": "event", "name": "slot.released", "seq": 1}
    state = reduce(
        [entry(1, "event.received", fact), entry(2, "state.changed", {**moved, "cause": cause})]
    )
    assert [(one.seq, one.name, one.source) for one in state.events] == [
        (1, "slot.released", "app")
    ]
    assert state.events[0].identity is None and state.app_state == {"slots": ["10:15"]}
