"""The AgentSession a written call runs on: a model, no ears, no voice, one answer per tool."""

from __future__ import annotations

from typing import Any

from livekit.agents.llm import LLM
from livekit.agents.voice import AgentSession
from livekit.agents.voice.turn import PreemptiveGenerationOptions, TurnHandlingOptions

# livekit bounds its own model→tools→model loop with this and, on the last step, forces a final
# answer with tool_choice="none". ONE is the whole round: the tool answers, the model says what
# came back, the caller writes next. At three the agent kept talking after it had finished — a
# booking on 2026-09-13 said the appointment back again, unprompted, because two more generations
# were still owed to it — and a written call is the same agent on a different line: the text
# session ran at eight and the spoken one at one, two ceilings for one idea (2026-09-26).
ONE_ANSWER_PER_TOOL = 1

# A written turn is complete the moment it arrives, so the channel says when the caller is done:
# every turn is a frame the caller sent, handed to generate_reply by hand, so livekit builds no
# detector and warns of no VAD. And no answer is started before the turn is in: preemption is for
# a voice that may still be speaking.
WRITTEN_PREEMPTION: PreemptiveGenerationOptions = {"enabled": False}
WRITTEN_TURNS: TurnHandlingOptions = {
    "turn_detection": "manual",
    "preemptive_generation": WRITTEN_PREEMPTION,
}


def a_written_session(llm: LLM[Any]) -> AgentSession[None]:
    """One written call's session: vad=None keeps livekit from building ears nobody listens with."""
    written: AgentSession[None] = AgentSession(
        llm=llm,
        vad=None,
        turn_handling=WRITTEN_TURNS,
        max_tool_steps=ONE_ANSWER_PER_TOOL,
    )
    return written
