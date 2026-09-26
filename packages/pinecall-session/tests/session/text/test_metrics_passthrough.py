"""Criterion 2, by name: the wire's metrics.llm entry is livekit's LLMMetrics, field by field."""

import time

import pytest
from livekit.agents.metrics import LLMMetrics as Measured
from livekit.agents.metrics.base import Metadata
from livekit.agents.metrics.usage import AgentSessionUsage, ModelUsageCollector

from pinecall.session.text.metrics import (
    Reply,
    llm_metrics,
    tokens_spent,
    turn_metrics,
    usage_rows,
)
from pinecall_protocol import encode
from pinecall_protocol.metrics import LLMMetrics

pytestmark = pytest.mark.unit

A_SPEECH = "sp_1"

# Every field livekit measures, with a value no default could be mistaken for.
EVERYTHING = Measured(
    label="livekit.plugins.anthropic.llm.LLM",
    request_id="req_1",
    timestamp=time.time(),
    duration=0.7,
    ttft=0.25,
    cancelled=False,
    completion_tokens=24,
    prompt_tokens=1200,
    prompt_cached_tokens=1024,
    cache_creation_tokens=176,
    reasoning_tokens=12,
    total_tokens=1224,
    tokens_per_second=34.2,
    speech_id=A_SPEECH,
    metadata=Metadata(model_name="claude-haiku-4-5-20251001", model_provider="anthropic"),
)


def test_the_entry_carries_every_field_the_library_class_declares() -> None:
    """The field list is read off livekit's own class, never off a list somebody typed here."""
    said = encode(llm_metrics(EVERYTHING, A_SPEECH))
    whole = EVERYTHING.model_dump()
    assert set(Measured.model_fields) - set(said) == set()
    for name in Measured.model_fields:
        assert said[name] == whole[name], name


def test_nothing_is_renamed_on_the_way_to_the_wire() -> None:
    """The wire's own model declares the same names, so a rename would fail validation, not pass."""
    assert set(Measured.model_fields) <= set(LLMMetrics.model_fields)


def test_a_count_no_provider_reported_is_absent_and_never_zero() -> None:
    """livekit leaves what nobody reported at its default; exclude_defaults is what keeps it out."""
    quiet = EVERYTHING.model_copy(update={"cache_creation_tokens": 0, "reasoning_tokens": 0})
    said = encode(llm_metrics(quiet, A_SPEECH))
    assert "cache_creation_tokens" not in said
    assert "reasoning_tokens" not in said
    assert said["prompt_cached_tokens"] == 1024, "a required field stays, whatever its value"


def test_the_speech_id_is_the_one_thing_the_session_adds() -> None:
    said = encode(llm_metrics(EVERYTHING, A_SPEECH))
    assert said["speech_id"] == A_SPEECH
    assert said["type"] == "llm_metrics"


def test_the_turns_two_numbers_are_read_off_the_library_and_not_computed() -> None:
    reply = Reply(speech_id=A_SPEECH, arrived=0.0)
    reply.measured(EVERYTHING)
    said = encode(turn_metrics(reply, e2e_latency=1.0, provider="anthropic", model="a-model"))
    assert said["llm_node_ttft"] == EVERYTHING.ttft
    assert said["llm_node_tps"] == EVERYTHING.tokens_per_second
    assert said["provider_request_ids"] == ["req_1"]


def test_a_request_that_generated_no_token_leaves_the_turns_ttft_out() -> None:
    """livekit says -1 for that, which is not a latency anybody may average."""
    silent = EVERYTHING.model_copy(update={"ttft": -1.0})
    reply = Reply(speech_id=A_SPEECH, arrived=0.0)
    reply.measured(silent)
    said = encode(turn_metrics(reply, e2e_latency=1.0, provider="anthropic", model="a-model"))
    assert "llm_node_ttft" not in said
    assert "llm_node_tps" not in said


# Spend is gone: livekit's own ModelUsageCollector is the summing, and its row IS our wire model,
# field for field, so call.summary revalidates it rather than adding anything up.
def test_the_usage_rows_are_the_libraries_counts_summed_and_nothing_derived() -> None:
    collector = ModelUsageCollector()
    collector.collect(EVERYTHING)
    collector.collect(EVERYTHING)
    row = usage_rows(AgentSessionUsage(model_usage=collector.flatten()))[0]
    assert row.provider == "anthropic"
    assert row.input_tokens == 2 * EVERYTHING.prompt_tokens
    assert row.input_cached_tokens == 2 * EVERYTHING.prompt_cached_tokens
    assert row.input_cache_creation_tokens == 2 * EVERYTHING.cache_creation_tokens
    assert row.output_tokens == 2 * EVERYTHING.completion_tokens


def test_the_tokens_spent_are_what_the_rows_read_and_wrote_together() -> None:
    collector = ModelUsageCollector()
    collector.collect(EVERYTHING)
    usage = AgentSessionUsage(model_usage=collector.flatten())
    assert tokens_spent(usage) == EVERYTHING.prompt_tokens + EVERYTHING.completion_tokens
    assert tokens_spent(AgentSessionUsage(model_usage=[])) == 0


def test_the_turns_text_is_the_deltas_as_the_caller_read_them() -> None:
    reply = Reply(speech_id=A_SPEECH, arrived=0.0)
    for delta in ["Lo", " siento", ", Ana."]:
        reply.delta(delta)

    assert reply.text == "Lo siento, Ana."


def test_a_round_after_a_tool_call_is_its_own_paragraph() -> None:
    reply = Reply(speech_id=A_SPEECH, arrived=0.0)
    reply.round()
    reply.delta("Voy a mirar la agenda.")
    reply.round()  # the tool ran, a request went out again
    reply.delta("Hay hueco el martes.")

    assert reply.text == "Voy a mirar la agenda.\nHay hueco el martes."
