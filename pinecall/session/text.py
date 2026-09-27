"""A written call on the same session: a message in, the reply out, and a call taken up again."""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from livekit.agents import llm

from pinecall.session.call import SPEECH
from pinecall.session.session import Session
from pinecall.wire import events as wire
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import UserTurnMetrics
from pinecall.wire.parts import EndedBy, EndReason


@dataclass(frozen=True)
class TakenUp:
    """What a written call had when its session went away: the history and where it stood."""

    history: llm.ChatContext
    started_at: float | None
    turns: int
    last_said: str
    speeches: int


# livekit's generate_reply never runs the end-of-turn hook, so the lookups of a written turn run
# here, under the written budget. Under a takeover or an open ask for a person, the message is
# written and the model is not asked.
async def hears(session: Session, said: str) -> str:
    """The contact's message into the call; the agent's reply, or nothing when a person has it."""
    call = session.call
    if call.a_person_has_the_line:
        await _unanswered(session, said)
        return ""
    for skipped in await session.lookups.turn_ended(said, None):
        await call.writing.write("error", skipped)
    reply = session.live.generate_reply(user_input=said)
    await reply
    return next(
        (
            item.text_content or ""
            for item in reversed(reply.chat_items)
            if isinstance(item, llm.ChatMessage) and item.role == "assistant"
        ),
        "",
    )


# The session's close writes the rest: call.ended, then the seal.
async def end(session: Session, reason: EndReason, by: EndedBy) -> None:
    """End a written call for this reason and close it."""
    session.hang_up(reason, by)
    await session.close()


# A reply cut before its turn.agent is not in the log, so it is not in the history either.
def taken_up(entries: Sequence[Entry]) -> TakenUp:
    """The history a written call had, rebuilt from its log in the order it was written."""
    history = llm.ChatContext.empty()
    started_at: float | None = None
    turns, last_said, speeches = 0, "", 0
    for entry in entries:
        speeches = max(speeches, _numbered(entry.data.get("speech_id")))
        match entry.type:
            case "call.started":
                started = wire.CallStarted.model_validate(entry.data)
                started_at = started.started_at
            case "turn.user":
                history.add_message(role="user", content=str(entry.data.get("text", "")))
            case "turn.agent":
                text = str(entry.data.get("text", ""))
                history.add_message(role="assistant", content=text)
                turns, last_said = turns + 1, text or last_said
            case "tool.call":
                history.items.append(_called(entry))
            case "tool.result":
                history.items.append(_answered(entry))
            case _:
                continue
    return TakenUp(history, started_at, turns, last_said, speeches)


async def resume(session: Session, taken: TakenUp) -> None:
    """Open the session on a call taken up again: no call.started, no greeting."""
    call = session.call
    call.turns, call.last_said, call.speeches = taken.turns, taken.last_said, taken.speeches
    if taken.started_at is not None:
        session.started_at = taken.started_at
    await session.resume(taken.history)


async def _unanswered(session: Session, said: str) -> None:
    call = session.call
    heard = wire.UserTurnEnded(
        speech_id=call.speech(), text=said, metrics=UserTurnMetrics.model_validate({})
    )
    await call.writing.write("turn.user", heard)
    told = session.agent.chat_ctx.copy()
    told.add_message(role="user", content=said)
    await session.agent.update_chat_ctx(told, exclude_invalid_function_calls=False)


def _numbered(speech: object) -> int:
    if isinstance(speech, str) and speech.startswith(SPEECH) and speech[len(SPEECH) :].isdigit():
        return int(speech[len(SPEECH) :])
    return 0


def _called(entry: Entry) -> llm.FunctionCall:
    called = wire.ToolCall.model_validate(entry.data)
    arguments = json.dumps(called.arguments, ensure_ascii=False)
    return llm.FunctionCall(call_id=called.call_id, name=called.name, arguments=arguments)


def _answered(entry: Entry) -> llm.FunctionCallOutput:
    error = entry.data.get("error")
    output = entry.data.get("output")
    said = error if error is not None else output
    text = said if isinstance(said, str) else json.dumps(said, ensure_ascii=False)
    return llm.FunctionCallOutput(
        call_id=str(entry.data.get("call_id", "")),
        name=str(entry.data.get("name", "")),
        output=text,
        is_error=error is not None,
    )
