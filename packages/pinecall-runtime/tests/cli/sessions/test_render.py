"""Acceptance 1: the golden fixture rendered, with every metric livekit measured still in it."""

import json
from typing import cast

import pytest

from pinecall.cli.sessions.render import (
    METRIC_GUTTER,
    compact,
    declared_order,
    medians_table,
    metric_lines,
    transcript,
)
from pinecall.log.latencies import TURN_TYPES, medians
from pinecall_protocol import decode_entries
from pinecall_protocol.envelope import Entry
from pinecall_protocol.fixtures import GOLDEN_LOG

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def golden() -> list[Entry]:
    return decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def printed(golden: list[Entry]) -> str:
    return "\n".join(transcript(golden))


def test_every_metric_field_of_every_turn_is_printed_under_its_livekit_name(
    golden: list[Entry], printed: str
) -> None:
    """The card's first criterion: not one field of a turn's metrics is dropped or renamed."""
    turns = [entry for entry in golden if entry.type in TURN_TYPES]
    assert turns, "the golden must carry turns for this to mean anything"
    for turn in turns:
        for name in _leaf_names(turn.data["metrics"], "metrics."):
            assert f"\n{METRIC_GUTTER}{name} " in printed, f"{turn.type} seq {turn.seq} lost {name}"


def test_every_metrics_entry_is_printed_whole_field_by_field(
    golden: list[Entry], printed: str
) -> None:
    """Every metrics.<block> entry, every field: the transcript is the record, not a summary."""
    blocks = [entry for entry in golden if entry.type.startswith("metrics.")]
    assert {entry.type for entry in blocks} >= {
        "metrics.llm",
        "metrics.stt",
        "metrics.tts",
        "metrics.vad",
        "metrics.eou",
        "metrics.eot",
        "metrics.interruption",
    }
    for block in blocks:
        for name in _leaf_names(block.data, ""):
            assert f"\n{METRIC_GUTTER}{name} " in printed, (
                f"{block.type} seq {block.seq} lost {name}"
            )


def test_every_metric_value_is_printed_beside_its_name(golden: list[Entry], printed: str) -> None:
    """A name with the wrong number beside it would pass the two tests above. This one would not."""
    for entry in golden:
        if not entry.type.startswith("metrics."):
            continue
        for name, value in _leaves(entry.data, ""):
            assert f"{name}  " in printed
            assert _shown(value) in printed, f"{entry.type} seq {entry.seq}: {name}"


def test_every_entry_of_the_golden_gets_one_head_line(golden: list[Entry], printed: str) -> None:
    """One entry per line, in the order the log has them, seq and seconds-since-the-first first."""
    heads = _heads_in_order(printed)
    assert len(heads) == len(golden)
    assert heads[0].split()[:3] == ["1", "+0.000", "call.ringing"]
    assert heads[-1].split()[:3] == ["125", "+53.461", "log.caught_up"]


def test_a_tool_call_a_tool_result_and_a_confirmation_carry_their_own_mark(
    golden: list[Entry], printed: str
) -> None:
    """Marked by a glyph in the first column: it is read where no colour reaches, ssh and grep."""
    marks = {
        entry.type: line[0]
        for entry, line in zip(golden, _heads_in_order(printed), strict=True)
        if entry.type in ("tool.call", "tool.result", "confirm.request", "confirm.granted")
    }
    assert marks == {
        "tool.call": "→",
        "tool.result": "←",
        "confirm.request": "!",
        "confirm.granted": "!",
    }
    assert all(line.startswith(" ") for line in _heads_in_order(printed)[:3])


def test_the_payload_is_compact_json_on_the_head_line(printed: str) -> None:
    """A payload is one line, so a log is greppable; the accents survive, so it is readable."""
    line = next(line for line in printed.splitlines() if " call.summary " in line)
    written = line[line.index("{") :]
    payload = json.loads(written)
    assert payload["outcome"].startswith("booked BK-5521")
    assert written == json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def test_the_table_at_the_foot_names_every_measure_the_turns_carried(golden: list[Entry]) -> None:
    """What `show` ends with: a row per measure, the reader's numbers under livekit's names."""
    rows = medians_table(medians(golden))
    assert rows[:2] == ["", "medians"]
    assert [row.split()[0] for row in rows[2:]] == [
        "transcription_delay",
        "end_of_turn_delay",
        "llm_node_ttft",
        "tts_node_ttfb",
        "e2e_latency",
    ]
    assert rows[-1].split()[1:] == ["0.940s", "over", "5", "turns"]


def test_a_log_with_no_turns_ends_without_a_table(golden: list[Entry]) -> None:
    """The table is an answer to a question the log can answer; silence is the honest empty case."""
    assert medians_table(medians([entry for entry in golden if entry.type == "agent.state"])) == []


def test_an_empty_log_renders_to_nothing() -> None:
    """No entries, no lines — and no crash reaching for the first entry's clock."""
    assert transcript([]) == []


def test_a_block_prints_in_the_order_the_schema_declares_and_not_the_order_it_arrived(
    golden: list[Entry],
) -> None:
    """jsonb hands keys back in its own order; the reading stays livekit's, whatever storage did."""
    block = next(entry for entry in golden if entry.type == "metrics.llm")
    shuffled = block.model_copy(update={"data": dict(reversed(list(block.data.items())))})
    printed = [line.split()[1] for line in metric_lines(shuffled)]
    declared = [
        name
        for name in declared_order("metrics.llm")
        if name in block.data and not isinstance(block.data[name], dict)
    ]
    assert [name for name in printed if "." not in name] == declared
    assert printed[0] == "type"
    assert printed[-1].startswith("metadata.")


def test_a_turn_prints_its_metrics_in_the_order_livekit_declares_them(golden: list[Entry]) -> None:
    """The same for a turn, whose order is the one on livekit's ChatMessage metrics."""
    turn = next(entry for entry in golden if entry.type == "turn.agent")
    printed = [line.split()[1].removeprefix("metrics.") for line in metric_lines(turn)]
    declared = [name for name in declared_order("turn.agent") if name in turn.data["metrics"]]
    assert [name for name in printed if "." not in name] == [
        name for name in declared if not isinstance(turn.data["metrics"][name], dict)
    ]


def _heads_in_order(printed: str) -> list[str]:
    """The head lines only: the metric lines under them are indented and are not entries."""
    return [line for line in printed.splitlines() if not line.startswith(METRIC_GUTTER)]


def _leaf_names(block: object, prefix: str) -> list[str]:
    return [name for name, _ in _leaves(block, prefix)]


def _leaves(block: object, prefix: str) -> list[tuple[str, object]]:
    """Every field of a metrics block, dotted through the objects, read straight off the fixture."""
    if not isinstance(block, dict):
        return []
    found: list[tuple[str, object]] = []
    for name, value in cast("dict[str, object]", block).items():
        if isinstance(value, dict):
            found.extend(_leaves(cast("object", value), f"{prefix}{name}."))
        else:
            found.append((f"{prefix}{name}", value))
    return found


def _shown(value: object) -> str:
    return value if isinstance(value, str) else compact(value)
