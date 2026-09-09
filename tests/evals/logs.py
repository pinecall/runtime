"""The hand-written call logs the judges are pinned against, and the tools they are read with."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from pinecall.log.entry import Entry
from pinecall.types import ToolSpec
from pinecall_protocol.codec import decode_entries
from pinecall_protocol.fixtures import GOLDEN_LOG

# The hand-written logs the judges and the ring-3 checks are pinned against, in one place: the
# question a judge asks and the question `pinecall eval` asks are the same question, and two sets
# of logs for it would drift the day somebody edits one of them.
LOGS = Path(__file__).parent / "logs"

# The protocol's own golden call: a whole conversation with retrieval, tools, confirmations, every
# typed metrics block and a summary. It is what the bridge is read against, because it is the only
# log that carries all of them at once.

# What Clínica Norte declares for the tool those logs call. `side_effect="irreversible"` is the
# whole reason consent has anything to look at: without a declaration a `tool.call` is a name.
BOOKING: Mapping[str, ToolSpec] = {
    "book_appointment": ToolSpec(
        name="book_appointment",
        description="Reserva una cita en la agenda de la clínica.",
        parameters={"type": "object", "properties": {"slot": {"type": "string"}}},
        side_effect="irreversible",
        confirm="Le reservo el {slot}. ¿Lo confirmo?",
    )
}

# What the golden call's own agent declares for the two tools that call uses. `find_slots` looks,
# `book_slot` books: one of them needs the caller's yes and the other never did.
THE_GOLDENS_TOOLS: Mapping[str, ToolSpec] = {
    "find_slots": ToolSpec(
        name="find_slots",
        description="Huecos libres de un doctor en un día.",
        parameters={
            "type": "object",
            "properties": {
                "doctor": {"type": "string"},
                "day": {"type": "string"},
                "period": {"type": "string"},
            },
        },
        side_effect="read",
    ),
    "book_slot": ToolSpec(
        name="book_slot",
        description="Reserva un hueco de la agenda para un paciente.",
        parameters={
            "type": "object",
            "properties": {
                "doctor": {"type": "string"},
                "at": {"type": "string"},
                "patient": {"type": "string"},
            },
        },
        side_effect="irreversible",
        confirm="Le reservo el {at}. ¿Confirmo?",
    ),
}

# The same tool declared read-only, for the other half of the rule: a tool that changes nothing
# needs nobody's yes, and consent must say so rather than stay quiet.
LOOKING_UP: Mapping[str, ToolSpec] = {
    "book_appointment": replace(BOOKING["book_appointment"], side_effect="read", confirm=None)
}


def a_log(name: str) -> list[Entry]:
    """One hand-written log by file name, from whichever of the two directories holds it."""
    for directory in (LOGS,):
        written = directory / f"{name}.json"
        if written.exists():
            return decode_entries(written.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"no hand-written log called {name}.json")


def the_golden_call() -> list[Entry]:
    """The repo's golden call log, whole, as both languages reduce it."""
    return decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))
