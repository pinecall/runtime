"""A tool's read-back: the receipt, spoken from inside the tool that earned it, and heard out."""

from __future__ import annotations

from typing import Any


# Said from inside the tool call (tools.py), which is where livekit documents speaking around a
# tool: "use session.say() inside the tool" (docs/agents/logic/tools/design). At that point the
# model is still waiting for this tool to return, so it has written nothing about the result and
# the receipt reaches the line first — preamble, receipt, reply.
#
# And AWAITED, which is the half that was missing. `say()` adds its sentence to the history only
# once its audio has finished playing (agent_activity.py:_tts_task_impl, after wait_for_playout),
# while the reply to a tool result is generated the instant the tool returns, from the history as
# it stands at that instant (agent_activity.py:_pipeline_reply_task, the LLM inference runs before
# the speech is authorized). A receipt fired and forgotten was therefore not in the history the
# model answered from: it wrote "Su cita queda confirmada para el miércoles…" never having seen
# "Reservado: el miércoles…", and the caller heard the booking twice, in two wordings, from one
# turn. Awaiting the handle is what livekit's own example does (examples/healthcare/agent.py) and
# it is allowed here: the guard on wait_for_playout refuses only the tool's OWN speech.
#
# It waited for a gap before, and a gap is the wrong signal: by the time the line went quiet the
# model had generated and queued its whole reply, so the receipt spoke after the agent had said
# "¿Alguna cosa más?" and handed the turn back. Queueing it earlier from OUTSIDE the tool was
# worse — it landed in the history the model was still writing against, which it answered, and
# answered again, to the `max_tool_steps` ceiling: four goodbyes in a row, two of them identical.
# Inside the tool there is no round in flight to interrupt.
async def read_back(live: Any, text: str) -> None:
    """Say the receipt now, from inside the tool, and return once the caller has heard it."""
    await live.say(text)
