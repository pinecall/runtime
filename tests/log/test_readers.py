"""Tests for the filters a reader asks for and the projections its credential allows."""

import json

import pytest

from pinecall.domain.agent import AgentConfig
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import JsonObject
from pinecall.log.readers import (
    ALWAYS_PASS,
    EVERYTHING,
    MASK,
    MAX_TYPES,
    PUBLIC_ENTRY_FIELDS,
    Filter,
    parse_filter,
    project_entry,
    project_state,
)
from pinecall.log.reduce import reduce
from pinecall.wire.frames import Entry, read_log
from tests.wire.golden import GOLDEN_LOG

GOLDEN = read_log(GOLDEN_LOG.read_text(encoding="utf-8"))
STATE = reduce(GOLDEN)
DECLARED = AgentConfig(
    "clinica-norte",
    state_fields={"slots": "public", "booking": "public", "patient": "pii"},
)

THE_CONTRACTS_ORDER = [
    "seq",
    "status",
    "user_state",
    "agent_state",
    "live",
    "turns",
    "app_state",
    "room",
    "confirms",
    "transfer",
    "held",
    "events",
]


def entry(kind: str, data: JsonObject, *, ephemeral: bool = False) -> Entry:
    return Entry(
        seq=11,
        ts=1_790_000_011.0,
        call="CA_31",
        agent="taller-oeste",
        type=kind,
        ephemeral=ephemeral,
        data=data,
    )


def public(item: Entry, viewer: str | None = None) -> JsonObject | None:
    return project_entry(item, "public", DECLARED, viewer)


# ── filters ──


def test_the_default_filter_narrows_nothing() -> None:
    assert all(EVERYTHING.passes(item) for item in GOLDEN)


def test_types_keeps_only_what_it_names() -> None:
    wanted = Filter(types=frozenset({"turn.user"}))
    assert {item.type for item in GOLDEN if wanted.passes(item)} == {"turn.user", *ALWAYS_PASS} & {
        item.type for item in GOLDEN
    }


def test_durable_drops_what_a_store_may_forget() -> None:
    durable = Filter(durable=True)
    assert not durable.passes(
        entry("user.transcript", {"text": "a", "final": False}, ephemeral=True)
    )
    assert durable.passes(entry("turn.user", {}))


@pytest.mark.parametrize("kind", sorted(ALWAYS_PASS))
def test_the_always_pass_set_survives_every_filter(kind: str) -> None:
    narrowest = Filter(types=frozenset({"custom"}), durable=True)
    assert narrowest.passes(entry(kind, {}, ephemeral=True))


def test_a_trailing_comma_is_not_a_type() -> None:
    assert parse_filter("turn.user,", durable=False).types == frozenset({"turn.user"})


def test_no_types_asked_is_every_type_and_durable_is_kept() -> None:
    assert parse_filter(None, durable=True) == Filter(durable=True)


def test_too_many_types_is_refused_by_the_number() -> None:
    many = ",".join(f"type.{n}" for n in range(MAX_TYPES + 1))
    with pytest.raises(DeclarationRefused, match=f"at most {MAX_TYPES} types, not {MAX_TYPES + 1}"):
        parse_filter(many, durable=False)


@pytest.mark.parametrize("name", ["Turn.User", "turn user", "turn;drop", "tür.n"])
def test_a_name_off_the_charset_is_refused(name: str) -> None:
    with pytest.raises(DeclarationRefused, match="lowercase words joined by dots"):
        parse_filter(name, durable=False)


# ── the state ──


def test_a_participant_reads_the_contracts_fields_in_the_contracts_order() -> None:
    assert list(project_state(STATE, "public", DECLARED)) == THE_CONTRACTS_ORDER


def test_a_participant_reads_only_the_state_fields_declared_public() -> None:
    seen = project_state(STATE, "public", DECLARED)
    assert seen["app_state"] == {"slots": ["09:30", "11:00"], "booking": "BK-5521"}
    assert "Marta Ruiz" not in json.dumps(seen)


def test_a_participant_never_sees_a_seats_attributes() -> None:
    seen = json.dumps(project_state(STATE, "public", DECLARED))
    assert "attributes" not in seen
    assert "sip." not in seen


def test_a_participant_reads_a_turn_by_its_words_and_an_agents_wait_only() -> None:
    seen = project_state(STATE, "public", DECLARED)["turns"]
    assert isinstance(seen, list)
    for turn in seen:
        assert isinstance(turn, dict)
        assert set(turn) <= {"role", "speech_id", "text", "interrupted", "metrics"}
        metrics = turn.get("metrics", {})
        assert isinstance(metrics, dict)
        assert set(metrics) <= {"e2e_latency"}


def test_a_participant_reads_the_events_it_sent_and_no_other() -> None:
    assert project_state(STATE, "public", DECLARED, viewer="web_1")["events"] == []
    mine = STATE.model_copy(deep=True)
    mine.events[0].source, mine.events[0].identity = "participant", "web_1"
    assert project_state(mine, "public", DECLARED, viewer="web_1")["events"] != []
    assert project_state(mine, "public", DECLARED, viewer="web_2")["events"] == []


def test_the_tenant_reads_everything_with_what_was_declared_pii_masked() -> None:
    seen = project_state(STATE, "tenant", DECLARED)
    assert seen == {**STATE.written(), "app_state": {**STATE.app_state, "patient": MASK}}


def test_an_agent_that_declared_nothing_shows_the_public_nothing_and_the_tenant_everything() -> (
    None
):
    assert project_state(STATE, "public", None)["app_state"] == {}
    assert project_state(STATE, "tenant", None) == STATE.written()


# ── one entry ──


def test_a_participant_reads_an_envelope_without_the_agent_or_the_call() -> None:
    for item in GOLDEN:
        seen = public(item)
        assert seen is None or set(seen) == {"seq", "ts", "type", "ephemeral", "data"}


def test_an_entry_type_the_contract_does_not_name_never_reaches_a_participant() -> None:
    unnamed = {item.type for item in GOLDEN} - set(PUBLIC_ENTRY_FIELDS)
    assert {"tool.call", "call.summary", "metrics.llm"} <= unnamed
    assert all(public(item) is None for item in GOLDEN if item.type in unnamed)


def test_a_participant_reads_only_the_fields_its_row_names() -> None:
    for item in GOLDEN:
        seen = public(item)
        if seen is not None and item.type not in {"state.changed", "log.gap"}:
            assert isinstance(seen["data"], dict)
            assert set(seen["data"]) <= set(PUBLIC_ENTRY_FIELDS[item.type])


def test_a_participant_reads_a_reply_with_the_wait_and_nothing_else_measured() -> None:
    reply: JsonObject = {
        "speech_id": "a1",
        "text": "Listo.",
        "interrupted": False,
        "metrics": {"e2e_latency": 0.8, "llm_node_ttft": 0.3},
    }
    seen = public(entry("turn.agent", reply))
    assert seen is not None
    assert seen["data"] == {
        "speech_id": "a1",
        "text": "Listo.",
        "interrupted": False,
        "metrics": {"e2e_latency": 0.8},
    }


def test_an_outside_fact_reaches_only_the_participant_who_sent_it() -> None:
    fact: JsonObject = {
        "name": "form.sent",
        "data": {"x": 1},
        "source": "participant",
        "identity": "web_1",
    }
    assert public(entry("event.received", fact), viewer="web_1") is not None
    assert public(entry("event.received", fact), viewer="web_2") is None
    assert public(entry("event.received", {**fact, "source": "app"}), viewer="web_1") is None


def test_a_state_change_reaches_a_participant_filtered_and_without_its_cause() -> None:
    moved: JsonObject = {
        "state": {"slots": ["12:00"], "patient": {"name": "Ana"}},
        "changed": ["slots", "patient"],
        "cause": {"kind": "tool", "tool": "hold", "call_id": "t9"},
    }
    seen = public(entry("state.changed", moved))
    assert seen is not None
    assert seen["data"] == {"state": {"slots": ["12:00"]}, "changed": ["slots"]}


def test_the_tenant_reads_the_state_an_entry_carries_with_pii_masked() -> None:
    moved: JsonObject = {
        "state": {"slots": ["12:00"], "patient": {"name": "Ana"}},
        "changed": ["patient"],
    }
    seen = project_entry(entry("state.changed", moved), "tenant", DECLARED)
    assert seen is not None
    assert seen["data"] == {**moved, "state": {"slots": ["12:00"], "patient": MASK}}
    assert seen["agent"] == "taller-oeste"


def test_a_gap_carries_its_snapshot_projected_for_the_same_reader() -> None:
    gap = entry(
        "log.gap", {"from_seq": 1, "to_seq": 10, "snapshot": STATE.written()}, ephemeral=True
    )
    seen_public = public(gap)
    seen_tenant = project_entry(gap, "tenant", DECLARED)
    assert seen_public is not None
    assert seen_tenant is not None
    assert isinstance(seen_public["data"], dict)
    assert isinstance(seen_tenant["data"], dict)
    assert seen_public["data"]["snapshot"] == project_state(STATE, "public", DECLARED)
    assert seen_tenant["data"]["snapshot"] == project_state(STATE, "tenant", DECLARED)


def test_an_old_shape_is_withheld_from_the_public_and_whole_for_the_tenant() -> None:
    old = entry("turn.agent", {"speech_id": "a1", "said": "Listo."})
    assert public(old) is None
    seen = project_entry(old, "tenant", DECLARED)
    assert seen is not None
    assert seen["data"] == old.data
