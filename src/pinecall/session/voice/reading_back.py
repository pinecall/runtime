"""A tool's read-back: the receipt, spoken from inside the tool that earned it."""

from __future__ import annotations

from typing import Any


# Said from inside the tool call (tools.py), which is where livekit documents speaking around a
# tool: "use session.say() inside the tool" (docs/agents/logic/tools/design). At that point the
# model is still waiting for this tool to return, so it has written nothing about the result and
# the receipt reaches the line first — preamble, receipt, reply.
#
# It waited for a gap before, and a gap is the wrong signal: by the time the line went quiet the
# model had generated and queued its whole reply, so the receipt spoke after the agent had said
# "¿Alguna cosa más?" and handed the turn back. Queueing it earlier from OUTSIDE the tool was
# worse — it landed in the history the model was still writing against, which it answered, and
# answered again, to the `max_tool_steps` ceiling: four goodbyes in a row, two of them identical.
# Inside the tool there is no round in flight to interrupt.
def read_back(live: Any, text: str) -> None:
    """Say the receipt now, from inside the tool: the model has not spoken about this yet."""
    live.say(text)
