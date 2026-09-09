"""What `sessions show` draws: a line per entry, every metric under the turn it timed, the table."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any, cast

from pinecall.log.entry import Entry
from pinecall.log.latencies import TURN_TYPES, Median
from pinecall_protocol._base import WireModel
from pinecall_protocol.registry import EVENTS

# A turn's metrics and a metrics block are read field by field underneath, so the head line carries
# what is left and nothing is shown twice. Which entries are turns is log/latencies.py's answer, and
# the table at the foot of this transcript is read off the very same list.
METRICS_PREFIX = "metrics."

# The mark column. A glyph, never a colour: this transcript is read over ssh, piped to grep, and
# pasted into a card, and none of those carry an escape sequence. → asks, ← answers, ! is the
# human gate. Every other entry gets a blank in the same column so the seqs stay in one line.
MARKS: dict[str, str] = {"tool.call": "→", "tool.result": "←"}
CONFIRM_MARK = "!"
NO_MARK = " "

# Column widths: a seq fits in four digits for any call a person reads, t in "+1234.567", and the
# longest event type today is `supervisor.transferred` at 22.
SEQ_WIDTH = 5
TIME_WIDTH = 9
TYPE_WIDTH = 22

# A metric line hangs under its entry behind a gutter, so a head line and a field are told apart
# by eye and by grep — an indent alone is not enough when the seq column is itself right-aligned.
METRIC_GUTTER = "    · "


def transcript(entries: Sequence[Entry]) -> list[str]:
    """Every entry as `seq t type payload`, with its metric fields underneath. Pure: no I/O."""
    if not entries:
        return []
    origin = entries[0].ts
    return [line for entry in entries for line in lines_of(entry, origin)]


def lines_of(entry: Entry, origin: float) -> list[str]:
    """One entry: its head line, then the fields that are read one per line rather than inline."""
    return [head_line(entry, origin), *metric_lines(entry)]


def head_line(entry: Entry, origin: float) -> str:
    """The line a reader scans: the mark, the seq, seconds since the log opened, the type, JSON."""
    payload = inline_payload(entry)
    columns = (
        f"{mark_of(entry.type)} "
        f"{entry.seq:>{SEQ_WIDTH}}  "
        f"{seconds_since(entry.ts, origin):>{TIME_WIDTH}}  "
        f"{entry.type:<{TYPE_WIDTH}}"
    )
    # No payload means the fields are printed underneath; the type column's padding would then
    # trail into nothing, and trailing space is what turns a paste into a diff.
    return f"{columns} {compact(payload)}" if payload else columns.rstrip()


def mark_of(type: str) -> str:
    """The glyph in the first column: a tool asking, a tool answering, a confirmation, or space."""
    if type.startswith("confirm."):
        return CONFIRM_MARK
    return MARKS.get(type, NO_MARK)


def seconds_since(ts: float, origin: float) -> str:
    """How far into the call this entry is, in seconds with milliseconds, signed so it lines up."""
    return f"+{ts - origin:.3f}"


def inline_payload(entry: Entry) -> dict[str, Any]:
    """What the head line still carries: everything the lines underneath do not print in full."""
    if entry.type in TURN_TYPES:
        return {name: value for name, value in entry.data.items() if name != "metrics"}
    if entry.type.startswith(METRICS_PREFIX):
        return {}
    return entry.data


def metric_lines(entry: Entry) -> list[str]:
    """A turn's metrics block, or a whole metrics entry, one field per line under livekit's name."""
    order = declared_order(entry.type)
    if entry.type in TURN_TYPES:
        return _fields(entry.data.get("metrics") or {}, prefix="metrics.", order=order)
    if entry.type.startswith(METRICS_PREFIX):
        return _fields(entry.data, prefix="", order=order)
    return []


def compact(value: Any) -> str:
    """JSON with no spaces to waste and the accents intact — the payload is read, not parsed."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fields(block: Any, *, prefix: str, order: tuple[str, ...] = ()) -> list[str]:
    """Every field of a block, flattened to a dotted name so no nested name is ever swallowed."""
    if not isinstance(block, dict):
        return []
    named = list(_flatten(cast("dict[str, Any]", block), prefix, order))
    if not named:
        return []
    width = max(len(name) for name, _ in named)
    return [f"{METRIC_GUTTER}{name.ljust(width)}  {_value(value)}" for name, value in named]


def _flatten(
    block: dict[str, Any], prefix: str, order: tuple[str, ...] = ()
) -> Iterator[tuple[str, Any]]:
    """A nested object becomes `outer.inner`; a list stays one value, its shape being the fact."""
    for name in _in_order(block, order):
        value = block[name]
        if isinstance(value, dict):
            yield from _flatten(cast("dict[str, Any]", value), f"{prefix}{name}.")
        else:
            yield f"{prefix}{name}", value


def _in_order(block: dict[str, Any], order: tuple[str, ...]) -> list[str]:
    """The declared names first, in livekit's order, then anything the block carried besides."""
    declared = [name for name in order if name in block]
    return declared + [name for name in block if name not in order]


def declared_order(type: str) -> tuple[str, ...]:
    """The field order the schema declares, so a jsonb round trip cannot reshuffle a reading."""
    model = EVENTS.get(type)
    if model is None:
        return ()
    if type in TURN_TYPES:
        return _fields_of(model.model_fields["metrics"].annotation)
    return tuple(model.model_fields)


def _fields_of(annotation: Any) -> tuple[str, ...]:
    """The names of a nested wire model, when that is what the field holds."""
    if isinstance(annotation, type) and issubclass(annotation, WireModel):
        return tuple(annotation.model_fields)
    return ()


def _value(value: Any) -> str:
    """A string as itself, everything else as JSON: 0.48 reads as a number and "es" as a word."""
    return value if isinstance(value, str) else compact(value)


def medians_table(rows: Sequence[Median]) -> list[str]:
    """The table `show` ends with. Empty when the call carried none of the five measures."""
    if not rows:
        return []
    width = max(len(row.name) for row in rows)
    return [
        "",
        "medians",
        *(
            f"  {row.name.ljust(width)}  {row.seconds:>8.3f}s  over {row.turns} turns"
            for row in rows
        ),
    ]
