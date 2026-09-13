"""A tool's read-back: the receipt, said before the agent's own account of what it did."""

from __future__ import annotations

from typing import Any


# WHEN it is said is the whole design, and waiting for a gap was the wrong answer.
#
# A read-back is queued the moment the tool answers, which is while the agent is still speaking the
# preamble it wrote alongside the tool call ("Perfecto, le confirmo el lunes a las cinco"). The
# model has not yet written its account of the result — it cannot have, it does not know the result
# yet — so a receipt queued now lands between the two: preamble, receipt, reply.
#
# Waiting for the line to go quiet put it last instead. By the time the gap arrived the model had
# already generated and queued the whole reply, ending in "¿Alguna cosa más?" — and the receipt
# spoke after the agent had handed the turn back. On 2026-09-13 a caller heard exactly that: asked
# if he needed anything else, and then told "Reservado: con la doctora Vidal" — a different doctor
# from the one he had just been promised. Two faults in one sentence, and the ordering made the
# real one look like the agent talking to itself.
#
# livekit's queue is a heap ordered by priority and then arrival (agent_activity.py:1826), and
# `say` and a model's reply both enter it at SPEECH_PRIORITY_NORMAL. So queueing cuts nothing off:
# it takes the place the arrival time earns, which here is exactly the place a receipt belongs.
def read_back(live: Any, text: str) -> None:
    """Queue the receipt now: after the sentence being spoken, before the reply not yet written."""
    live.say(text)
