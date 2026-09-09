"""What a text session measures of itself: nothing. livekit's LLMMetrics is the measurement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from livekit.agents.metrics import LLMMetrics as Measured
from livekit.agents.metrics.usage import AgentSessionUsage

from pinecall.providers.usage import as_wire_rows
from pinecall_protocol.metrics import (
    AgentTurnMetrics,
    LLMMetrics,
    LLMModelUsage,
    TurnMetadata,
)


# One reply as it is built: the rounds accumulate here, and turn.agent is written from it.
@dataclass
class Reply:
    """What a reply has said and spent so far, across however many rounds of tools it took."""

    speech_id: str
    arrived: float
    said: list[str] = field(default_factory=list[str])
    request_ids: list[str] = field(default_factory=list[str])
    ttft: float | None = None
    tps: float | None = None

    def round(self) -> None:
        """A request is going out: what it says is a round of its own."""
        self.said.append("")

    def delta(self, text: str) -> None:
        """One delta of the current round, as the caller read it: no separator, no newline."""
        if not self.said:
            self.round()
        self.said[-1] += text

    @property
    def text(self) -> str:
        """Everything the model said this turn, one paragraph per round, an empty round left out."""
        return "\n".join(spoken for spoken in self.said if spoken)

    def measured(self, metrics: Measured) -> None:
        """Fold one round's metrics in. The first round that produced a token is the turn's."""
        if metrics.request_id:
            self.request_ids.append(metrics.request_id)
        # livekit says -1 for a request that generated no token at all; a turn's ttft is the first
        # round that actually produced one, and a second round after a tool never resets it.
        if self.ttft is None and metrics.ttft >= 0:
            self.ttft = metrics.ttft
            self.tps = metrics.tokens_per_second


# model_dump(exclude_defaults=True) is the whole of the pass-through: livekit gives every field it
# measured a value and leaves the ones no provider reported at their default, so a count nobody
# reported is absent from the entry rather than a zero somebody could add up. `type` is the one
# default that must survive, because an entry without its tag cannot be read back through its
# discriminated union.
def llm_metrics(metrics: Measured, speech_id: str) -> LLMMetrics:
    """One LLM request as livekit measured it, on the wire under livekit's own names."""
    said: dict[str, Any] = metrics.model_dump(exclude_defaults=True)
    said["type"] = "llm_metrics"
    said["speech_id"] = speech_id
    return LLMMetrics.model_validate(said)


# tts_node_ttfb, playback_latency and the two speaking timestamps belong to audio this path never
# produces, so they are absent. e2e_latency is measured from the moment the caller's text arrived,
# which in a text session is the only clock the caller can feel.
def turn_metrics(reply: Reply, e2e_latency: float, provider: str, model: str) -> AgentTurnMetrics:
    """What this session measured of one reply: livekit's numbers, and what audio would add."""
    said: dict[str, Any] = {
        "e2e_latency": e2e_latency,
        "provider_request_ids": list(reply.request_ids),
        "llm_metadata": TurnMetadata(model_name=model, model_provider=provider),
    }
    if reply.ttft is not None:
        said["llm_node_ttft"] = reply.ttft
        said["llm_node_tps"] = reply.tps
    return AgentTurnMetrics(**said)


# livekit's own ModelUsageCollector keeps the rows, one per provider and model, and
# `providers/usage.py` is the one place they become ours. A text call runs no ears and no voice,
# so the LLM rows are all there is to keep and the rest would be an empty line in the summary.
def usage_rows(usage: AgentSessionUsage) -> list[LLMModelUsage]:
    """What the session consumed, as call.summary carries it: livekit's rows, unchanged."""
    return [row for row in as_wire_rows(usage.model_usage) if isinstance(row, LLMModelUsage)]
