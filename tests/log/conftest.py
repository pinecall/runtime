"""What the log tests share: an org, a judgment, an entry, and a call already logged."""

from dataclasses import dataclass
from uuid import uuid4

import pytest

from pinecall.domain.agent import Versions
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim, Store
from pinecall.wire.frames import Entry

THE_DAY = 0.0

AGENT = "dental-sur"


@dataclass(frozen=True)
class ACall:
    """How a test call went: who called, on which channel, what came of it, what was judged."""

    agent: str = AGENT
    channel: str = "phone"
    caller: str = "+34 600 111 222"
    outcome: str = "booked a visit"
    cost: float = 0.25
    judges: tuple[JsonObject, ...] = ()
    took_over: bool = False
    scope: Scope | None = None
    persona: str | None = None
    ended: bool = True
    versions: Versions | None = None
    # What else it logged before it ended, each a type and its data.
    logged: tuple[tuple[str, JsonObject], ...] = ()


@pytest.fixture
def org() -> str:
    return f"org-{uuid4().hex[:10]}"


def judgment(name: str, verdict: str, reason: str = "") -> JsonObject:
    return {
        "name": name,
        "verdict": verdict,
        "criteria": "c",
        "reason": reason,
        "evidence": {"seqs": []},
    }


def entry(kind: str, data: JsonObject, *, ts: float = 5.0, ephemeral: bool = False) -> Entry:
    return Entry(seq=1, ts=ts, call="CA_x", agent=AGENT, type=kind, ephemeral=ephemeral, data=data)


async def logged_call(store: Store, org: str, went: ACall | None = None) -> str:
    """Write a claimed call the way a session does, and return its id."""
    went = went or ACall()
    agent = went.agent
    call = f"CA_{uuid4().hex[:12]}"
    await store.claim(
        call, agent, org, Claim(went.scope or Scope(org), went.versions or Versions())
    )
    line: JsonObject = {
        "channel": went.channel,
        "from": went.caller,
        "to": "+34910000000",
        "caller": None,
    }
    started: JsonObject = {
        **line,
        "direction": "inbound",
        "started_at": 1.0,
        "persona": went.persona,
    }
    await store.append(
        call,
        agent,
        "call.ringing",
        {**line, "route": {"channel": went.channel, "number": "+34910000000"}},
        ephemeral=False,
    )
    await store.append(call, agent, "call.started", started, ephemeral=False)
    await store.append(
        call,
        agent,
        "turn.user",
        {"speech_id": "u1", "text": "hola", "metrics": {}},
        ephemeral=False,
    )
    reply: JsonObject = {
        "speech_id": "a1",
        "text": "buenas",
        "interrupted": False,
        "metrics": {"e2e_latency": 1.5},
    }
    await store.append(call, agent, "turn.agent", reply, ephemeral=False)
    if went.took_over:
        await store.append(
            call, agent, "supervisor.took_over", {"by": {"id": "m_1", "name": "I"}}, ephemeral=False
        )
    for kind, data in went.logged:
        await store.append(call, agent, kind, data, ephemeral=False)
    if went.ended:
        ended_with: JsonObject = {
            "reason": "caller_hung_up",
            "ended_by": "caller",
            "ended_at": 9.0,
            "duration_s": 8.0,
        }
        await store.append(call, agent, "call.ended", ended_with, ephemeral=False)
        summary: JsonObject = {
            "reason": "caller_hung_up",
            "outcome": went.outcome,
            "duration_s": 8.0,
            "turns": 2,
            "usage": [],
            "cost": {
                "usd": went.cost,
                "rows": [],
                "unpriced": [],
            },
        }
        await store.append(call, agent, "call.summary", summary, ephemeral=False)
        score: JsonObject = {"judges": list(went.judges), "judge_calls": 0}
        await store.append(call, agent, "call.score", score, ephemeral=False)
        await store.seal(call)
    return call
