"""Every block livekit measures reaches the log with every field, and a cancelled one is priced."""

from __future__ import annotations

import asyncio
import typing
from typing import Any

import pytest
from livekit.agents.metrics import LLMMetrics, base
from livekit.agents.metrics.usage import AgentSessionUsage, ModelUsageCollector

from pinecall.providers import prices
from pinecall.session.voice.log_writer import Writing
from pinecall.session.voice.metrics import BLOCKS, Meters, an_end_of_utterance
from pinecall_protocol import decode_entry, event_of
from tests.session.voice.fakes import CALL, Recording

pytestmark = pytest.mark.unit

# One value per field type, chosen so that a default could never be mistaken for it.
DISTINCT: dict[Any, Any] = {str: "seen", float: 1.25, int: 7, bool: True}


def a_block(model: type[Any]) -> Any:
    """An instance of a livekit metrics class with every field set to something distinct."""
    # A nested class is annotated by a string livekit resolves lazily; base's namespace resolves it.
    hints = typing.get_type_hints(model, globalns=dict(vars(base)))
    said: dict[str, Any] = {}
    for name in model.model_fields:
        if name == "type":
            continue
        said[name] = _a_value(hints[name])
    return model(**said)


def _a_value(annotation: Any) -> Any:
    """A distinct value of the annotated type: a nested model is built the same way."""
    candidates = [
        one for one in (typing.get_args(annotation) or (annotation,)) if one is not type(None)
    ]
    wanted = candidates[0]
    if isinstance(wanted, type) and hasattr(wanted, "model_fields"):
        return a_block(wanted)
    return DISTINCT.get(wanted, DISTINCT[str])


def every_block() -> list[type[Any]]:
    return list(typing.get_args(base.AgentMetrics))


def test_the_table_names_every_block_the_library_declares() -> None:
    assert set(BLOCKS) >= set(every_block())


@pytest.mark.parametrize("model", every_block(), ids=lambda model: model.__name__)
async def test_every_field_of_a_block_reaches_the_log_under_its_own_name(model: type[Any]) -> None:
    """Criterion 1, the second half: built on the library's own class, dumped whole."""
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    block = a_block(model)
    Meters(writing).collected(block)
    await asyncio.sleep(0)
    await writing.flushed()
    (written,) = recording.entries
    assert written.type == BLOCKS[model][0]
    assert written.data == block.model_dump(mode="json")
    # And it reads back through the protocol's own registry, as a console would read it.
    entry = decode_entry(
        {
            "seq": 1,
            "ts": 0.0,
            "call": CALL,
            "agent": "x",
            "type": written.type,
            "ephemeral": False,
            "data": written.data,
        }
    )
    assert event_of(entry).model_dump(mode="json") == block.model_dump(mode="json")


async def test_a_cancelled_block_from_a_discarded_preemptive_generation_is_logged_and_priced() -> (
    None
):
    """Criterion 3: preemptive generation is on by default in 1.8 (voice/turn.py:223) and a
    discarded attempt still emits LLMMetrics with cancelled=True — the tokens were spent."""
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    meters = Meters(writing)
    discarded = LLMMetrics(
        label="livekit.plugins.anthropic.llm.LLM",
        request_id="req_discarded",
        timestamp=0.0,
        duration=0.4,
        ttft=0.2,
        cancelled=True,
        completion_tokens=8,
        prompt_tokens=1200,
        prompt_cached_tokens=1000,
        total_tokens=1208,
        tokens_per_second=20.0,
        metadata=base.Metadata(model_name="claude-haiku-4-5-20251001", model_provider="anthropic"),
    )
    # The bridge hears the block off the llm; livekit's own collector, the one the session feeds
    # from the same event (agent_activity.py:1981), sums it into the rows the summary carries.
    meters.collected(discarded)
    collector = ModelUsageCollector()
    collector.collect(discarded)
    meters.collected_usage(AgentSessionUsage(model_usage=collector.flatten()))
    await asyncio.sleep(0)
    await writing.flushed()
    (block,) = recording.of("metrics.llm")
    assert block.data["cancelled"] is True
    assert block.data["prompt_tokens"] == 1200
    rows = meters.rows
    assert [row.model for row in rows] == ["claude-haiku-4-5-20251001"]
    cost = prices.cost_of(rows)
    assert cost.eur > 0
    assert {row.unit for row in cost.rows} == {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
    }


# ── the meter a streaming STT keeps running (livekit-metrics.md #18) ────────────────

SONIOX = base.Metadata(model_name="stt-rt-v5", model_provider="Soniox")


def a_usage_tick_of(audio_duration: float) -> base.STTMetrics:
    """One RECOGNITION_USAGE tick, shaped as the soniox plugin emits it on every frame."""
    return base.STTMetrics(
        label="livekit.plugins.soniox.stt.STT",
        request_id="",
        timestamp=0.0,
        duration=0.0,
        audio_duration=audio_duration,
        streamed=True,
        metadata=SONIOX,
    )


async def test_the_ticks_of_a_streaming_stt_ride_the_stream_and_the_totals_are_stored() -> None:
    """A scripted stream of interims and one final: the ticks are ephemeral, the connection
    block is stored whole, and the call's audio seconds survive in livekit's own usage row."""
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    meters = Meters(writing)
    collector = ModelUsageCollector()

    connection = base.STTMetrics(
        label="livekit.plugins.deepgram.stt.STT",
        request_id="",
        timestamp=0.0,
        duration=0.0,
        audio_duration=0.0,
        streamed=True,
        acquire_time=0.31,
        connection_reused=False,
        metadata=SONIOX,
    )
    ticks = [a_usage_tick_of(0.12) for _ in range(9)] + [a_usage_tick_of(0.48)]
    for block in [connection, *ticks]:
        meters.collected(block)
        collector.collect(block)
    meters.collected_usage(AgentSessionUsage(model_usage=collector.flatten()))
    await asyncio.sleep(0)
    await writing.flushed()

    written = recording.of("metrics.stt")
    assert [entry.ephemeral for entry in written] == [None] + [True] * len(ticks)
    assert written[0].data["acquire_time"] == 0.31
    (row,) = [one for one in meters.rows if one.type == "stt_usage"]
    assert row.audio_duration == pytest.approx(sum(tick.audio_duration for tick in ticks))


async def test_a_transcription_that_was_measured_once_is_stored_whole() -> None:
    """The other half of #18: a block that measured a request — a non-streamed `recognize()` —
    has a real duration and is kept, the way every other block of a turn is."""
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    measured_once = base.STTMetrics(
        label="livekit.plugins.openai.stt.STT",
        request_id="req_1",
        timestamp=0.0,
        duration=0.42,
        audio_duration=2.5,
        streamed=False,
        metadata=SONIOX,
    )
    Meters(writing).collected(measured_once)
    await asyncio.sleep(0)
    await writing.flushed()

    (entry,) = recording.of("metrics.stt")
    assert entry.ephemeral is None
    assert entry.data == measured_once.model_dump(mode="json")


def test_the_turns_own_transcription_delay_is_what_a_reader_reads_instead() -> None:
    """No STTMetrics carries a speech_id, so a turn's STT number is the one on its own report:
    `transcription_delay`, rebuilt into the EOU block the log stores whole."""
    eou = an_end_of_utterance(
        {"transcription_delay": 0.18, "end_of_turn_delay": 0.62}, "speech_1", "manual"
    )
    assert eou is not None
    assert eou.transcription_delay == 0.18
    assert eou.end_of_utterance_delay == 0.62
