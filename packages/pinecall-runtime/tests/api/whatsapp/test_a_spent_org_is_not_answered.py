"""A thread whose org runs out of a quota mid-conversation ends there, and answers nothing more."""

from __future__ import annotations

import httpx
import pytest
from livekit.agents import llm as agents

from pinecall.api.agents.registry import Registry
from pinecall.api.whatsapp.threads import Threads
from pinecall.log.store import MemoryStore
from pinecall.orgs.records import MemoryOrgs
from pinecall.routes.records import MemoryRoutes
from pinecall.types import Quotas
from tests.api.conftest import A_RECORD, AGENT
from tests.api.fake_graph import FakeGraph
from tests.api.whatsapp.conftest import (
    ANA,
    THE_CLINICS_NUMBER,
    a_body,
    a_text,
    delivered,
    quiet,
    the_clinic_answers_at_the_number,
)
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

A_HUNDRED_TOKENS = agents.CompletionUsage(completion_tokens=20, prompt_tokens=80, total_tokens=100)


async def test_the_turn_past_the_token_quota_closes_the_thread_unanswered(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    orgs: MemoryOrgs,
    store: MemoryStore,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    await orgs.set_quotas(A_RECORD.org, Quotas(llm_tokens=100))
    llm.script.append(Scripted(chunks=("Hola, dígame.",), usage=A_HUNDRED_TOKENS))
    await the_clinic_answers_at_the_number(registry, routes)
    await delivered(meta, a_body(a_text("Hola")))
    thread = await quiet(threads)
    await delivered(meta, a_body(a_text("¿Y mañana?")))
    await thread.idle()
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None
    assert [text for *_, text in graph.sent] == ["Hola, dígame."]
    assert llm.requests == 1
    refusals = [one for one in await store.agent_since(AGENT) if one.type == "credits.exhausted"]
    assert refusals[-1].data["quota"] == "llm_tokens"
