"""One real exchange with Haiku: the adapter, the stream, and a real provider's usage block."""

from datetime import date

import pytest

from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.providers.models import models_for
from pinecall.session.text.session import TextSession
from pinecall.settings import load_settings
from pinecall.types import NOTHING_BROUGHT, AgentConfig, CallContext, Model, Route

pytestmark = pytest.mark.needs_llm

A_CALL = "call_one_real_exchange"
AGENT = "clinica-norte"

# The cheapest model that can hold a conversation, and the shortest thing worth asking it.
HAIKU = Model(provider="anthropic", model="claude-haiku-4-5-20251001")


async def test_haiku_answers_and_the_metrics_carry_what_the_provider_reported() -> None:
    store = MemoryStore()
    context = CallContext(
        call=A_CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=date.today(),
    )
    config = AgentConfig(slug=AGENT)
    model = models_for(load_settings())(HAIKU, NOTHING_BROUGHT)
    session = TextSession(context, config, CallLog(store, AGENT, A_CALL), model)
    await session.start()
    await session.set_prompt("identity", "Sos Clara. Respondé en una sola palabra.")
    await session.hears("¿Cuál es la capital de Uruguay?")
    await session.hangup("caller_hung_up", "caller")

    written = await store.since(A_CALL)
    said = next(entry for entry in written if entry.type == "turn.agent")
    measured = next(entry for entry in written if entry.type == "metrics.llm")
    summary = written[-1]
    assert "montevideo" in said.data["text"].lower()
    assert measured.data["prompt_tokens"] > 0
    assert measured.data["completion_tokens"] > 0
    assert measured.data["ttft"] > 0
    assert "anthropic" in measured.data["metadata"]["model_provider"]
    assert measured.data["speech_id"] == said.data["speech_id"]
    # livekit's anthropic plugin answers `provider` with the API host (`llm.py:150` returns the
    # client's base-url netloc), so the row is labelled `api.anthropic.com` and never "anthropic".
    # Pricing does not care: PRICES is keyed by model prefix, so the row is priced all the same.
    assert summary.data["usage"][0]["provider"] == "api.anthropic.com"
    assert summary.data["cost"]["unpriced"] == []
    assert summary.data["cost"]["eur"] > 0
