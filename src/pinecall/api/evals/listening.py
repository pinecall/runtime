"""When the agent has finished answering: the one signal a spoken caller waits for between lines."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pinecall.evals.polling import until
from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall_protocol.defs import AgentState
from pinecall_protocol.events import AgentStateChanged
from pinecall_protocol.registry import EventType

# How long the caller holds the line open after its last word. A golden whose expectation is a
# tool on the last turn — most of them — is judged on whether the agent got there, so the line
# cannot drop when the caller stops talking: the first spoken run of `identifica-al-paciente`
# came back "ran no tool at all" for exactly that, on a call that had not finished. Thirty and not
# twenty because a turn that runs a tool speaks twice, and the two TTS rounds around it measured
# thirteen seconds on 2026-09-11.
AN_ANSWER_MAY_TAKE_S = 30.0

# How long the line is watched before a quiet agent is believed. Longer than the gap between
# a state changing and its line reaching the log, short enough to sit inside a caller's own
# pause between two sentences.
A_BEAT_S = 0.75

# What livekit publishes about itself, and the one of its five words that means "I have finished
# and it is your turn". Both typed by the protocol, so a misspelling is a type error and not a
# run that never hangs up.
AGENT_STATE: EventType = "agent.state"
IT_IS_LISTENING: AgentState = "listening"


# The one wait every spoken caller makes between two of its lines, and after its last: the golden
# runner and `simulate --voice` alike. Before this was shared the simulated persona slept six fixed
# seconds and spoke over any answer that ran a tool (2026-09-11, heard on the recordings).
async def until_the_answer_lands(store: Store, call: str, said: int) -> None:
    """Hold the line until the agent has answered the last line, or until it plainly will not."""

    async def landed() -> bool:
        if not the_answer_has_landed(await whole(store, call), said):
            return False
        # Asked twice, a beat apart, because `agent.state` reaches the log a moment after the
        # agent changed and the log is all this can see. Once was not enough: on 2026-09-13 the
        # persona spoke over `Muy bien. Voy` — the agent had been `speaking` since seq 330 and the
        # line went out anyway, and the reply that was announcing a registration died mid-word.
        # A single reading cannot tell a call that is over from one whose last state has not
        # landed yet; two, a pause apart, can. The cost is that pause, once per turn.
        await asyncio.sleep(A_BEAT_S)
        return the_answer_has_landed(await whole(store, call), said)

    await until(landed, within_s=AN_ANSWER_MAY_TAKE_S)


# Why neither a count of turns nor a tool round is the signal. `en-el-chat-ofrece-mas-de-dos-horas`,
# spoken, 2026-09-11: the agent called freeSlots, then SAID "voy a consultar qué hay libre el
# lunes" — a filler, spoken after the tool had already answered, because the model writes its
# preamble and its tool call in one response and livekit speaks that preamble once the round is
# done. That filler is a `turn.agent` landing after the tool, so a run watching for either one hung
# up on it: 234 milliseconds later, in the middle of the generation that had the hours in it. On
# the call before it the real answer arrived one second after the caller had already gone.
#
# The signal is the one livekit publishes about itself and the log already carries: the agent goes
# `thinking`, `speaking`, and back to `listening` when it has nothing left to say. A filler leaves
# it thinking. So the line is held until it is listening again, and that is neither a guess nor a
# count of anything.
def the_answer_has_landed(entries: Sequence[Entry], said: int) -> bool:
    """Every line heard, and the agent back to listening after the last of them."""
    heard = [at for at, entry in enumerate(entries) if entry.type == "turn.user"]
    if len(heard) < said:
        return False
    if _a_tool_is_still_running(entries):
        return False
    states = [(at, entry) for at, entry in enumerate(entries) if entry.type == AGENT_STATE]
    if not states:
        return False
    at, last = states[-1]
    return at > heard[-1] and AgentStateChanged.model_validate(last.data).state == IT_IS_LISTENING


# `listening` is not the same as finished. An agent that says "Perfecto, la doy de alta" and calls
# a tool in the same response goes quiet WHILE the tool runs, and livekit publishes that quiet as
# `listening` — there is nothing else it could say about it. Both interruptions on 2026-09-13 were
# exactly that: `Perfecto, la doy` cut while registerPatient ran, `Entendido. Voy` cut while
# freeSlots ran. A caller who hears a preamble waits for what it was a preamble TO, and a tool
# that was asked for and not yet answered is the one thing that says the turn is not over.
def _a_tool_is_still_running(entries: Sequence[Entry]) -> bool:
    """Whether a tool was asked for and has not answered: the agent is mid-turn, however quiet."""
    asked = {entry.data.get("call_id") for entry in entries if entry.type == "tool.call"}
    answered = {entry.data.get("call_id") for entry in entries if entry.type == "tool.result"}
    return bool(asked - answered)
