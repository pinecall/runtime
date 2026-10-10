"""When the caller may speak: the agent's answer read off the log, spoken or written."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from pinecall.log.logs import Logs, Subscription
from pinecall.wire.events import AgentStateChanged
from pinecall.wire.frames import Entry

# The caller's next line and whether it hangs up after it, given the turns left.
type NextLine = Callable[[int], Awaitable[tuple[str, bool]]]


LISTENING = "listening"


# Longer than the lag between a state and its entry, shorter than a caller's pause.
A_BEAT_S = 0.75


# The quiet a person leaves once the other has stopped. An answer holds only after it: a tool's
# call, or the request that follows a tool's result, lands on the log a moment after the agent
# went back to `listening`, and a caller that spoke in that moment talked over the answer.
A_QUIET_AFTER_S = 1.0


# An agent that listens in silence this long, nothing of it landing on the log, has no answer:
# the caller goes on. An agent speaking or thinking, or a tool of its running, is waited for.
A_SILENCE_MAY_LAST_S = 30.0


# The most one answer is waited for whatever the agent is doing: a worker that hung mid-turn.
AN_ANSWER_MAY_TAKE_S = 180.0


# An agent that only listens this long opens with nothing.
A_SILENT_OPENING_S = 3.0


AN_OPENING_MAY_TAKE_S = 15.0


def is_call_over(entries: Sequence[Entry]) -> bool:
    """Whether somebody hung up: every end lands as call.ended."""
    return any(entry.type == "call.ended" for entry in entries)


def is_line_open(entries: Sequence[Entry], now: float) -> bool:
    """Whether the agent finished its opening, or has none, and listens."""
    states = [(index, entry) for index, entry in enumerate(entries) if entry.type == "agent.state"]
    if not states:
        return False
    spoke = [index for index, entry in enumerate(entries) if entry.type == "turn.agent"]
    index, last = states[-1]
    if spoke:
        return _state_of(last) == LISTENING and index > spoke[-1]
    only_listened = all(_state_of(entry) == LISTENING for _, entry in states)
    return only_listened and now - states[0][1].ts >= A_SILENT_OPENING_S


# Turn counts lie: livekit speaks a tool's preamble as a turn of its own, and some ears end a
# turn per sentence. A state alone lies too: livekit publishes `listening` while a tool runs, and
# between one tool and the next. The agent having SPOKEN since the caller's last line, and being
# back to listening after it with no tool running, is the signal.
def has_answer_landed(entries: Sequence[Entry], lines: int, *, since: float) -> bool:
    """Whether every line reached the agent, and it spoke and listens again after the last."""
    heard = [index for index, entry in enumerate(entries) if entry.type == "turn.user"]
    # A snapshot older than the caller's silence would answer with the turn before.
    if not heard or entries[heard[-1]].ts < since or len(heard) < lines:
        return False
    if _is_a_tool_running(entries):
        return False
    spoke = [index for index, entry in enumerate(entries) if entry.type == "turn.agent"]
    if not spoke or spoke[-1] < heard[-1]:
        return False
    states = [(index, entry) for index, entry in enumerate(entries) if entry.type == "agent.state"]
    if not states:
        return False
    index, last = states[-1]
    return index > spoke[-1] and _state_of(last) == LISTENING


def is_agent_busy(entries: Sequence[Entry]) -> bool:
    """Whether the agent is on the line: thinking, speaking, or a tool of its running."""
    if _is_a_tool_running(entries):
        return True
    states = [entry for entry in entries if entry.type == "agent.state"]
    return bool(states) and _state_of(states[-1]) != LISTENING


# Read off the log as it grows, never on a timer: a fixed wait talked over tool answers, and a
# cap on the whole answer talked over a long one. Only an agent silent on the line is given up on.
# The log's own clock stamps its entries, so it is the clock the caller's silence is read by:
# `since` is when the caller began its line, before the agent could have heard it.
async def answered(logs: Logs, call: str, lines: int, *, since: float | None = None) -> None:
    """Wait until the agent answered `lines` lines (its opening when 0), or the call ended."""
    if since is None:
        since = logs.store.clock()
    subscription = await logs.reading(call).followed()
    loop = asyncio.get_running_loop()
    started = loop.time()
    moved_at = started
    opening = lines == 0
    quiet_after = A_BEAT_S if opening else A_QUIET_AFTER_S
    at_most = AN_OPENING_MAY_TAKE_S if opening else AN_ANSWER_MAY_TAKE_S
    try:
        while loop.time() - started < at_most:
            entries = await logs.store.whole(call)
            if is_call_over(entries):
                return
            done = (
                is_line_open(entries, logs.store.clock())
                if opening
                else has_answer_landed(entries, lines, since=since)
            )
            quiet = loop.time() - moved_at
            # A state reaches the log late: an answer holds only once the quiet has passed.
            if done and quiet >= quiet_after:
                return
            silent = not opening and not done and not is_agent_busy(entries)
            if silent and quiet >= A_SILENCE_MAY_LAST_S:
                return
            if await _moved(subscription, A_BEAT_S):
                moved_at = loop.time()
    finally:
        subscription.close()


async def speak_turns(
    turns: int,
    next_line: NextLine,
    say: Callable[[str], Awaitable[None]],
    wait: Callable[[int], Awaitable[None]],
) -> int:
    """Say each line once the agent answered the last, the first after its opening; the count."""
    spoken = 0
    await wait(0)
    for turn in range(turns):
        line, hanging_up = await next_line(turns - turn)
        if not line:
            break
        await say(line)
        spoken += 1
        if hanging_up:
            break
        await wait(spoken)
    return spoken


def _state_of(entry: Entry) -> str:
    return AgentStateChanged.model_validate(entry.data).state


# livekit publishes `listening` while a tool runs, so a call with no result yet is mid-turn.
def _is_a_tool_running(entries: Sequence[Entry]) -> bool:
    called = {str(entry.data.get("call_id")) for entry in entries if entry.type == "tool.call"}
    answered_ids = {
        str(entry.data.get("call_id")) for entry in entries if entry.type == "tool.result"
    }
    return bool(called - answered_ids)


async def _moved(subscription: Subscription, within_s: float) -> bool:
    try:
        await asyncio.wait_for(anext(subscription), within_s)
    except (TimeoutError, StopAsyncIteration):
        return False
    return True
