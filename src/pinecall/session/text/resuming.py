"""A text call taken up again from its log: the history the model reads, its state, its counters."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from livekit.agents import llm as agents

from pinecall.log.entry import Entry

# The speech ids a text session mints, `sp_<n>`: numbering goes on after the last one the log has,
# so a turn after the restart is never filed under an id a turn before it already holds.
SPEECH = re.compile(r"^sp_(\d+)$")


@dataclass
class TakenUp:
    """Everything a text session holds that its log can give back."""

    history: list[agents.ChatItem] = field(default_factory=list[agents.ChatItem])
    state: dict[str, Any] = field(default_factory=dict[str, Any])
    turns: int = 0
    last: str = ""
    speeches: int = 0
    started_at: float | None = None
    # When the call last wrote anything: how long it has been quiet, for a thread's idle clock.
    last_at: float = 0.0


# The model's history is the conversation as it was said — the caller's turns, the agent's, and
# every tool it ran with what came back — in the order the log wrote it. What is not in the log
# (a reply cut before its turn.agent) is not in the history either, which is what the caller saw.
def taken_up(entries: Sequence[Entry]) -> TakenUp:
    """What the log says this call had reached, as a text session holds it."""
    taken = TakenUp()
    for entry in entries:
        data = entry.data
        taken.last_at = max(taken.last_at, entry.ts)
        taken.speeches = max(taken.speeches, _speech_number(data.get("speech_id")))
        if entry.type == "call.started":
            started = data.get("started_at")
            taken.started_at = float(started) if isinstance(started, int | float) else entry.ts
        elif entry.type == "turn.user":
            taken.turns += 1
            taken.history.append(
                agents.ChatMessage(role="user", content=[str(data.get("text", ""))])
            )
        elif entry.type == "turn.agent":
            text = str(data.get("text", ""))
            taken.last = text or taken.last
            taken.history.append(agents.ChatMessage(role="assistant", content=[text]))
        elif entry.type == "tool.call":
            taken.history.append(
                agents.FunctionCall(
                    call_id=str(data["call_id"]),
                    name=str(data["name"]),
                    arguments=json.dumps(data.get("arguments", {})),
                )
            )
        elif entry.type == "tool.result":
            failed = data.get("error")
            said = failed if failed is not None else data.get("output")
            taken.history.append(
                agents.FunctionCallOutput(
                    call_id=str(data["call_id"]),
                    name=str(data.get("name", "")),
                    output=said if isinstance(said, str) else json.dumps(said),
                    is_error=failed is not None,
                )
            )
        elif entry.type == "state.changed":
            taken.state = dict(data.get("state", {}))
    return taken


def _speech_number(speech: object) -> int:
    """The n of `sp_<n>`, or 0 for anything else."""
    matched = SPEECH.match(speech) if isinstance(speech, str) else None
    return int(matched.group(1)) if matched else 0
