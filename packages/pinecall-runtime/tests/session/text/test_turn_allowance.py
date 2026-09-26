"""A written turn is asked for before the model answers it, with what the call has spent so far."""

import re
from datetime import date

import pytest
from livekit.agents import llm

from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.session.text.session import TextSession
from pinecall.session.text.turn_allowance import Allowance, TurnRefused
from pinecall.types import AgentConfig, CallContext, Route
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_test_runs"
AGENT = "clinica-norte"
REFUSED = "org clinica has used 150 of its 100 llm_tokens: credits.exhausted"
# One answer that read 80 tokens and wrote 20, reported the way a real provider streams it.
A_HUNDRED = llm.CompletionUsage(completion_tokens=20, prompt_tokens=80, total_tokens=100)


def a_session(store: MemoryStore, model: FakeLLM, allowance: Allowance) -> TextSession:
    """One web call for an agent with nothing declared, held to this allowance."""
    context = CallContext(
        call=A_CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=date(2026, 9, 8),
    )
    log = CallLog(store, AGENT, A_CALL)
    return TextSession(context, AgentConfig(slug=AGENT), log, model, allowance=allowance)


class Counting:
    """An allowance that remembers what it was asked and refuses past a number of tokens."""

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        self.asked: list[tuple[int, int]] = []

    async def __call__(self, turns: int, tokens: int) -> str | None:
        self.asked.append((turns, tokens))
        return REFUSED if tokens >= self.tokens else None


async def test_each_turn_is_asked_with_the_turns_taken_and_the_tokens_spent_before_it() -> None:
    model = FakeLLM(Scripted(chunks=("Hola.",), usage=A_HUNDRED), Scripted(chunks=("Sí.",)))
    allowance = Counting(tokens=1_000)
    session = a_session(MemoryStore(), model, allowance)
    await session.start()
    await session.hears("hola")
    await session.hears("¿mañana?")
    # Turns as call.summary counts them — the caller's and the agent's — which is what the Meter
    # folds into `messages`, so the call in progress is counted in the same unit as the ones before.
    assert allowance.asked == [(0, 0), (2, 100)]


async def test_a_refused_turn_ends_the_call_by_the_platform_and_the_model_is_never_asked() -> None:
    store = MemoryStore()
    model = FakeLLM(Scripted(chunks=("Hola.",), usage=A_HUNDRED), Scripted(chunks=("Sí.",)))
    session = a_session(store, model, Counting(tokens=100))
    await session.start()
    await session.hears("hola")
    with pytest.raises(TurnRefused, match=re.escape("llm_tokens: credits.exhausted")):
        await session.hears("¿mañana?")
    assert model.requests == 1
    written = await store.since(A_CALL)
    ended = next(entry for entry in written if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("timeout", "platform")
    assert [entry.data["text"] for entry in written if entry.type == "turn.user"] == ["hola"]


async def test_a_session_nobody_limited_answers_every_turn() -> None:
    model = FakeLLM(Scripted(chunks=("Hola.",), usage=A_HUNDRED))
    session = TextSession(
        CallContext(
            call=A_CALL,
            channel="web",
            direction="inbound",
            caller="web_someone",
            route=Route(org="clinica", agent=AGENT, channel="web", number=None),
            today=date(2026, 9, 8),
        ),
        AgentConfig(slug=AGENT),
        CallLog(MemoryStore(), AGENT, A_CALL),
        model,
    )
    await session.start()
    await session.hears("hola")
    assert model.requests == 1
