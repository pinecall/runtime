"""Today's date as the model reads it: a tool call it appears to have made, and the answer."""

from __future__ import annotations

import json
from datetime import date

from livekit.agents import llm as agents
from livekit.agents.voice import Agent

from pinecall.session.history import remembered

# The name the model sees in its own transcript. A pair is the only way a date survives the trip:
# a system message appended mid-conversation is rewritten as a user turn for every JSON-object
# provider (_provider_format/utils.py:49), and the model would read today's date as something the
# caller said. A call and its output are grouped and sent as themselves.
CLOCK_TOOL = "current_date"

# One pair per call, so the id is a constant of the call and never a counter nobody increments.
CLOCK_CALL_ID = "clock_1"

# The day of the week by number, so the string does not change with the process's locale.
WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def dated(today: date) -> tuple[agents.FunctionCall, agents.FunctionCallOutput]:
    """The pair that puts today in the history: the call, and the answer that carries the date."""
    call = agents.FunctionCall(call_id=CLOCK_CALL_ID, name=CLOCK_TOOL, arguments="{}")
    answered = json.dumps({"today": today.isoformat(), "weekday": WEEKDAYS[today.weekday()]})
    # reply_required is False because nothing was asked: the output is context, not a turn owed an
    # answer, and a realtime model would otherwise speak the moment it read it.
    output = agents.FunctionCallOutput(
        call_id=CLOCK_CALL_ID,
        name=CLOCK_TOOL,
        output=answered,
        is_error=False,
        reply_required=False,
    )
    return call, output


async def seeded(agent: Agent, today: date) -> None:
    """Put the pair in the history once, before the caller has said anything at all."""
    await remembered(agent, *dated(today))
