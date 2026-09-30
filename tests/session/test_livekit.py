"""Tests for what livekit hands the session, read as ours."""

import time
from typing import get_args

import pytest
from livekit.agents import (
    ConversationItemAddedEvent,
    SessionUsageUpdatedEvent,
    UserInputTranscribedEvent,
    llm,
    metrics,
)
from livekit.agents.language import LanguageCode
from livekit.agents.metrics import AgentSessionUsage

from pinecall.domain.agent import (
    AgentConfig,
    PromptBlock,
)
from pinecall.domain.errors import DeclarationRefused
from pinecall.log.store import Store
from pinecall.providers.build import Running, thinking_of
from pinecall.session import text
from pinecall.session._livekit import BLOCKS, end_of_utterance, switch_of, switches_written
from pinecall.session.call import Writing
from pinecall.wire.commands import (
    PromptSet,
)
from pinecall.wire.frames import Entry
from pinecall.wire.rest.calls import BatchedEntry
from tests.conftest import postgres
from tests.session.conftest import (
    Box,
    a_session,
    heard_live,
    kinds,
    model_of,
)
from tests.session.test_session import NOBODY, a_block, settled


@postgres
async def test_a_static_block_rewrites_the_instructions_and_the_log_keeps_its_hash(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY, ["ok"])
    await session.start()
    await session.apply(PromptSet(name="identity", text="Sos la recepción de la clínica."))
    await text.hears(session, "hola")
    await text.end(session, "caller_hung_up", "caller")
    first = model_of(session).requests[0].items[0]
    assert "Sos la recepción" in str(getattr(first, "text_content", ""))
    changed = next(entry for entry in await store.whole(call) if entry.type == "prompt.changed")
    assert changed.data["chars"] == len("Sos la recepción de la clínica.")
    assert "recepción" not in str(changed.data)


@postgres
async def test_a_block_the_agent_never_declared_is_refused_and_leaves_no_entry(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(
        box, AgentConfig(slug="clinica-norte", prompt=(PromptBlock("identity", "static"),))
    )
    await session.start()
    with pytest.raises(DeclarationRefused, match="no block named 'view'"):
        await session.apply(PromptSet(name="view", text="x"))
    await text.end(session, "caller_hung_up", "caller")
    assert "prompt.changed" not in await kinds(store, call)


@postgres
async def test_a_block_is_written_after_every_listener_saw_it_so_the_speech_id_is_there(
    box: Box,
) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    block = metrics.TTSMetrics(
        label="acme", request_id="r1", timestamp=time.time(), ttfb=0.2, duration=1.0,
        audio_duration=1.0, cancelled=False, characters_count=12, streamed=True,
    )  # fmt: skip
    model_of(session).emit("metrics_collected", block)
    block.speech_id = "speech_9"
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    written = [entry for entry in seen if entry.type == "metrics.tts"]
    assert written[0].data["speech_id"] == "speech_9"


@postgres
async def test_a_user_turn_carries_the_language_the_recogniser_said_and_its_eou_block(
    box: Box, store: Store, call: str
) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    session.live.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="hola", is_final=True, language=LanguageCode("es")),
    )
    heard = llm.ChatMessage(
        role="user",
        content=["hola"],
        metrics={"end_of_turn_delay": 0.4, "transcription_delay": 0.1},
    )
    session.live.emit("conversation_item_added", ConversationItemAddedEvent(item=heard))
    await text.end(session, "caller_hung_up", "caller")
    entries = await store.whole(call)
    written = [entry.type for entry in entries]
    assert written.index("metrics.eou") < written.index("turn.user")
    turn = next(entry for entry in entries if entry.type == "turn.user")
    assert turn.data["language"] == "es"


def test_the_table_names_every_block_the_library_declares() -> None:
    declared = set(get_args(metrics.AgentMetrics))
    assert {livekits for livekits, _, _ in BLOCKS} == declared
    assert {kind for _, kind, _ in BLOCKS} == {
        "metrics.llm",
        "metrics.stt",
        "metrics.tts",
        "metrics.vad",
        "metrics.eou",
        "metrics.eot",
        "metrics.interruption",
        "metrics.realtime",
        "metrics.avatar",
    }


@postgres
async def test_every_field_of_a_block_reaches_the_log_under_its_own_name(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    block = a_block()
    model_of(session).emit("metrics_collected", block)
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    (written,) = [entry.data for entry in seen if entry.type == "metrics.llm"]
    for name, value in block.model_dump(mode="json").items():
        assert written[name] == value


@postgres
async def test_a_cancelled_block_from_a_discarded_generation_is_logged(box: Box) -> None:
    seen = heard_live(box)
    session = a_session(box, NOBODY)
    await session.start()
    model_of(session).emit("metrics_collected", a_block(cancelled=True))
    await settled()
    await text.end(session, "caller_hung_up", "caller")
    (written,) = [entry.data for entry in seen if entry.type == "metrics.llm"]
    assert written["cancelled"] is True


@postgres
async def test_a_row_livekit_grew_a_field_for_is_still_the_calls_usage(box: Box) -> None:
    session = a_session(box, NOBODY)
    await session.start()
    heard = metrics.STTModelUsage(provider="acme", model="acme-ears", audio_duration=3.0)
    session.live.emit(
        "session_usage_updated",
        SessionUsageUpdatedEvent(usage=AgentSessionUsage(model_usage=[heard])),
    )
    await text.end(session, "caller_hung_up", "caller")
    ((usage, _),) = box.sealed
    assert [(row.type, row.model) for row in usage] == [("stt_usage", "acme-ears")]


def test_the_end_of_utterance_block_carries_its_type_on_the_wire() -> None:
    report = {"end_of_turn_delay": 0.5, "transcription_delay": 0.0}
    block = end_of_utterance(report, "speech_1")
    assert block is not None
    assert (block.written()["type"], block.written()["end_of_utterance_delay"]) == (
        "eou_metrics",
        0.5,
    )


def test_a_user_turn_report_without_delays_is_no_end_of_utterance_block() -> None:
    assert end_of_utterance({"other": 1}, "speech_1") is None


async def nothing_sent(entries: list[BatchedEntry], *, after: int) -> list[Entry]:
    """A log that is never reached: the writer is not opened in these tests."""
    raise AssertionError((entries, after))


async def test_a_default_that_fails_writes_which_vendor_went_down_and_which_serves_now(
    acme: str,
) -> None:
    down = Running(acme, "k1", options={"refusal": "down"})
    backup = Running(acme, "k2", model="acme-2", options={"replies": [["hola"]]})
    thinking = thinking_of(Running(acme, "k1", options=down.options, fallbacks=(backup,)))
    writing = Writing(nothing_sent, "CA_1")
    switches_written(writing, (thinking,))
    chat = llm.ChatContext.empty()
    chat.add_message(role="user", content="hola")
    async with thinking.chat(chat_ctx=chat) as stream:
        async for _ in stream:
            continue
    entry, _ = writing.queued.get_nowait()
    assert entry.type == "vendor.switched"
    assert entry.data == {
        "stage": "llm",
        "vendor": acme,
        "model": "acme-1",
        "available": False,
        "serving": acme,
        "serving_model": "acme-2",
    }


def test_an_event_that_is_not_a_vendor_changing_writes_nothing(acme: str) -> None:
    backup = Running(acme, "k2", model="acme-2")
    thinking = thinking_of(Running(acme, "k1", fallbacks=(backup,)))
    assert isinstance(thinking, llm.FallbackAdapter)
    assert switch_of("llm", object(), thinking) is None


def test_a_stage_with_no_fallback_is_not_listened_to(acme: str) -> None:
    writing = Writing(nothing_sent, "CA_1")
    switches_written(writing, (thinking_of(Running(acme, "k1")),))
    assert writing.queued.empty()
