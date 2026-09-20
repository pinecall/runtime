"""What an agent's settings resolve to: every knob from the nearest corner that sets it."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter

from pinecall.types import THE_ORGS_OWN, Kept, Tuning, whose

# The adapter a row is read back through and written out through: the same device
# api/agents/endpoints.py hands a worker its config by. The column holds JSON and no meaning;
# what a knob may be is providers/tuning.py's, the one place that knows a vendor.
TUNING: TypeAdapter[Tuning] = TypeAdapter(Tuning)


# What "set" means, once, for the column and for the fall-through alike: a knob is set when it is
# in the row, and it is in the row when it is not None. A knob set to a FALSY value is set — a
# `hangup {when: ""}`, a `turn {endpointing_ms: 0}`, a `bases []` — and only an absent one is
# missing. There is no exception: `bases []` used to be dropped here, which made "I read no base,
# ignore the team's" the same row as "I never attached one", and both fell through.
def as_json(tuning: Tuning) -> dict[str, Any]:
    """The tuning as the column holds it: every knob that is set, and none that is not."""
    dumped: dict[str, Any] = TUNING.dump_python(tuning, mode="json", exclude_none=True)
    return dumped


def corners(holder: str | None) -> tuple[str, ...]:
    """The corners a key reads through, nearest first: its own, then the org's own."""
    mine = whose(holder)
    return (mine,) if mine == THE_ORGS_OWN else (mine, THE_ORGS_OWN)


# The ONE fall-through, and the only place a chain of corners becomes one tuning: both stores
# read through it and no door resolves anything of its own. Every knob falls through ON ITS OWN —
# a corner supplies the knobs its row has and the corner below supplies the rest, so setting your
# own voice no longer disconnects you from the team's llm, stt, memory, knowledge and bases. That
# is what taking the whole row of the first corner did, and `clear` did it with an empty row: an
# empty row supplies nothing and is invisible here, which is what makes cleared mean cleared.
# The version, author, note and holder answered are the NEAREST corner that supplied a knob —
# the version a call records and a screen shows beside what it reads.
def resolved(chain: Sequence[Kept[Tuning]]) -> Kept[Tuning] | None:
    """One tuning out of the corners, nearest first: each knob from the nearest row that sets it.

    None when no corner in the chain sets a single knob.
    """
    supplying = [(row, config) for row in chain if (config := as_json(row.value))]
    if not supplying:
        return None
    merged: dict[str, Any] = {}
    for _row, config in reversed(supplying):
        merged.update(config)
    nearest, _its_own = supplying[0]
    return dataclasses.replace(nearest, value=TUNING.validate_python(merged))
