"""The prompt on a text call: the same seam a spoken call cuts at, the same bytes at the model."""

import pytest
from livekit.agents import llm as agents

from pinecall.log.store import MemoryStore
from pinecall.session.text.session import TextSession
from pinecall.types import DeclarationRefused
from tests.session.fake_llm import FakeLLM, Scripted
from tests.session.text.test_session import A_CALL, a_session

pytestmark = pytest.mark.unit

STATIC = "Sos Clara, de Clínica Norte. Nunca inventes un turno."
A_VIEW = "Llama Ana. Hay dos turnos libres."


async def test_the_view_reaches_the_model_last_and_never_touches_the_instructions() -> None:
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    session = a_session(MemoryStore(), llm)
    await session.start()
    await session.set_prompt("identity", STATIC)
    await session.set_prompt("view", A_VIEW)
    await session.hears("hola")
    await session.set_prompt("view", "Llama Ana. Queda un turno libre.")
    await session.hears("¿y ahora?")
    first, second = llm.asked
    assert first.instructions == second.instructions == STATIC
    assert first.system.endswith(A_VIEW)
    assert second.system.endswith("Queda un turno libre.")
    assert isinstance(second.chat_ctx.items[-1], agents.ChatMessage)


async def test_the_same_instructions_twice_is_not_a_rewrite() -> None:
    """Rewriting the prefix with the same bytes still pays a cache write, so it is not done."""
    session = a_session(MemoryStore(), FakeLLM())
    await session.start()
    # The activity records the configuration it started with as one such item (:1155); what a
    # prompt.set adds comes after it.
    at_start = len(_the_rewrites_of(session))
    await session.set_prompt("identity", STATIC)
    await session.set_prompt("identity", STATIC)
    await session.set_prompt("view", A_VIEW)
    assert _the_rewrites_of(session)[at_start:] == [STATIC]
    await session.set_prompt("identity", "Sos Clara. Decí menos.")
    assert _the_rewrites_of(session)[at_start:] == [STATIC, "Sos Clara. Decí menos."]


async def test_a_block_the_agent_never_declared_is_refused_and_leaves_no_entry() -> None:
    store = MemoryStore()
    session = a_session(store, FakeLLM())
    await session.start()
    with pytest.raises(DeclarationRefused, match="'faq'"):
        await session.set_prompt("faq", "Abrimos a las nueve.")
    written = await store.since(A_CALL)
    assert "prompt.changed" not in [entry.type for entry in written]


def _the_rewrites_of(session: TextSession) -> list[str]:
    """Every instructions rewrite livekit recorded in the history (agent_activity.py:601)."""
    return [
        str(item.instructions)
        for item in session.text_agent.chat_ctx.items
        if isinstance(item, agents.AgentConfigUpdate) and item.instructions is not None
    ]
