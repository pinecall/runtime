"""The latencies a call carried: livekit's own names, read off the two entries a turn lands as."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, cast

from pinecall.log.entry import Entry

# A turn lands as one of two entries, and they are the only ones carrying a metrics block of their
# own. Named here because both readers of that block — the CLI's table and the eval's verdict —
# start by asking which entries are turns.
TURN_TYPES: tuple[str, ...] = ("turn.user", "turn.agent")

# livekit's own names, in the order a turn happens: the caller stops, the words arrive, the turn is
# called over, the model starts, the voice starts, the caller hears it. One list, because the CLI
# renders a table of them and `pinecall eval` judges a budget over the same numbers.
MEASURES: tuple[str, ...] = (
    "transcription_delay",
    "end_of_turn_delay",
    "llm_node_ttft",
    "tts_node_ttfb",
    "e2e_latency",
)


@dataclass(frozen=True)
class Median:
    """One measure over one call: its livekit name, the median, and how many turns carried it."""

    name: str
    seconds: float
    turns: int


def samples(entries: Sequence[Entry]) -> dict[str, tuple[float, ...]]:
    """Every value each measure was given, in turn order. A measure no turn carried has no key."""
    found: dict[str, list[float]] = {name: [] for name in MEASURES}
    for entry in entries:
        if entry.type not in TURN_TYPES:
            continue
        block = _the_metrics_of(entry.data.get("metrics"))
        for name in MEASURES:
            value = block.get(name)
            # A bool is an int in Python and would average to something; a measure it is not.
            if isinstance(value, int | float) and not isinstance(value, bool):
                found[name].append(float(value))
    return {name: tuple(values) for name, values in found.items() if values}


# Median and not mean: one interrupted turn moves an average, and what a person is asking is what a
# normal turn felt like.
def medians(entries: Sequence[Entry]) -> list[Median]:
    """A row per measure some turn carried. A measure nobody measured has no row, never a zero."""
    taken = samples(entries)
    return [
        Median(name=name, seconds=median(taken[name]), turns=len(taken[name]))
        for name in MEASURES
        if name in taken
    ]


def _the_metrics_of(block: Any) -> dict[str, Any]:
    """A turn's metrics, or an empty block: an entry that carried none measures nothing."""
    return cast("dict[str, Any]", block) if isinstance(block, dict) else {}
