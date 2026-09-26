"""The opening: the rule that a greeting is one verb, and the turn it lands as when a call opens."""

from datetime import date

import pytest

from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.session import greeting
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext, DeclarationRefused, Greeting, Route
from pinecall.types.agent import GREETING_IS_ONE_VERB
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_test_runs"
AGENT = "clinica-norte"
A_TUESDAY = date(2026, 9, 8)
THE_WORDS = "Clínica Norte, buenos días."


def a_session(store: MemoryStore, llm: FakeLLM, opening: Greeting | None) -> TextSession:
    """One web call for an agent that declared nothing but its name and how it opens."""
    context = CallContext(
        call=A_CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=A_TUESDAY,
    )
    config = AgentConfig(slug=AGENT, greeting=opening)
    return TextSession(context, config, CallLog(store, AGENT, A_CALL), llm)


# ── the rule ────────────────────────────────────────────────────────────────────


def test_a_greeting_that_names_neither_verb_is_refused_at_declaration() -> None:
    with pytest.raises(DeclarationRefused) as refused:
        Greeting()
    assert str(refused.value) == GREETING_IS_ONE_VERB.format(said="neither was")


def test_a_greeting_that_names_both_verbs_is_refused_at_declaration() -> None:
    """Both is not a greeting twice: it is a class that has not decided which one it means."""
    with pytest.raises(DeclarationRefused) as refused:
        Greeting(say=THE_WORDS, reply="saluda")
    assert str(refused.value) == GREETING_IS_ONE_VERB.format(said="both were declared")


# ── which verb the session runs ─────────────────────────────────────────────────


async def test_words_run_the_verbatim_verb_and_nothing_else() -> None:
    ran: list[tuple[str, str, bool | None]] = []

    async def said(text: str, interruptible: bool | None) -> None:
        ran.append(("say", text, interruptible))

    async def replied(instructions: str, interruptible: bool | None) -> None:
        ran.append(("reply", instructions, interruptible))

    await greeting.open_the_call(
        Greeting(say=THE_WORDS, allow_interruptions=False), say=said, reply=replied
    )
    assert ran == [("say", THE_WORDS, False)]


async def test_an_instruction_runs_the_model_and_never_the_verbatim_verb() -> None:
    ran: list[str] = []

    async def said(_text: str, _interruptible: bool | None) -> None:
        ran.append("say")

    async def replied(instructions: str, _interruptible: bool | None) -> None:
        ran.append(instructions)

    await greeting.open_the_call(Greeting(reply="saluda y preséntate"), say=said, reply=replied)
    assert ran == ["saluda y preséntate"]


async def test_a_class_that_declared_no_greeting_says_nothing_at_all() -> None:
    """The default is silence: the caller spoke first, and answering is the model's job."""

    async def refuse(words: str, _interruptible: bool | None) -> None:
        raise AssertionError(f"nothing should have been spoken, and {words!r} was")

    await greeting.open_the_call(None, say=refuse, reply=refuse)


# ── the turn it lands as ────────────────────────────────────────────────────────


async def test_a_written_call_opens_with_the_words_before_the_caller_says_anything() -> None:
    store = MemoryStore()
    session = a_session(store, FakeLLM(), Greeting(say=THE_WORDS))
    await session.start()
    written = await store.since(A_CALL)
    spoken = [entry for entry in written if entry.type == "turn.agent"]
    assert [entry.data["text"] for entry in spoken] == [THE_WORDS]
    # call.started first: the opening is a turn INSIDE the call, and a reader of the log finds it
    # where every other thing the agent said is, with nothing before the call it belongs to.
    assert [entry.type for entry in written] == ["call.started", "turn.agent", "agent.state"]


async def test_a_written_call_that_improvises_its_opening_runs_one_model_turn() -> None:
    store = MemoryStore()
    llm = FakeLLM(Scripted(chunks=("Clínica Norte, ¿en qué puedo ayudarle?",)))
    session = a_session(store, llm, Greeting(reply="saluda y preséntate"))
    await session.start()
    spoken = [entry for entry in await store.since(A_CALL) if entry.type == "turn.agent"]
    assert [entry.data["text"] for entry in spoken] == ["Clínica Norte, ¿en qué puedo ayudarle?"]
