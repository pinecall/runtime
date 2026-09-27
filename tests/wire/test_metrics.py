"""Tests for the metrics: every livekit-agents block and usage row, verbatim."""

from pinecall.wire import metrics
from pinecall.wire.metrics import LLMMetrics
from pinecall_protocol import metrics as their_metrics
from tests.wire.parity import mismatches


def test_every_metric_is_the_generated_one_field_for_field() -> None:
    assert mismatches(metrics, their_metrics) == []


def test_a_metric_block_names_its_kind_by_itself() -> None:
    block = LLMMetrics.read(
        {
            "label": "anthropic.LLM",
            "request_id": "r1",
            "timestamp": 1.0,
            "duration": 0.8,
            "ttft": 0.3,
            "cancelled": False,
            "completion_tokens": 20,
            "prompt_tokens": 900,
            "prompt_cached_tokens": 800,
            "total_tokens": 920,
            "tokens_per_second": 25.0,
        },
        "metrics.llm",
    )
    assert block.type == "llm_metrics"
