"""A stage a setting changes, tried once outside a call: a vendor's no is heard where it is set."""

import asyncio
import contextlib

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, APIError, APITimeoutError
from livekit.agents.llm import ChatContext

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.providers import voices
from pinecall.providers.build import Modality, Running, llm_of, stt_of

# A vendor slower than this to say a word is not refusing the setting, it is not answering.
TRY_S = 12.0

LINE = "OK."

# Ears that keep listening once the input ends took the connection: a refusal comes back sooner.
LISTENED_S = 4.0

# Half a second of silence at the rate every plugin takes, to open the ears and close them.
RATE = 16_000

REFUSED = "{vendor} refused the {stage} this sets: {why}"

UNANSWERED = (
    "{vendor} did not answer while the {stage} this sets was tried: {why}; nothing was kept, "
    "try again"
)

STAGE_NAMES: dict[Modality, str] = {"llm": "model", "stt": "ears", "tts": "voice"}

# Once, at once: a setting is not worth livekit's retries, and a person is waiting on the answer.
ONCE = APIConnectOptions(max_retry=0, timeout=10.0)

SILENCE = rtc.AudioFrame(
    data=bytes(RATE), sample_rate=RATE, num_channels=1, samples_per_channel=RATE // 2
)


async def tried(stage: Modality, running: Running) -> None:
    """The stage asked one small thing; the vendor's no refused, its silence UpstreamFailed."""
    named = STAGE_NAMES[stage]
    try:
        async with asyncio.timeout(TRY_S):
            await _ask(stage, running)
    except TimeoutError as slow:
        why = f"no answer in {TRY_S:.0f} s"
        unanswered = UNANSWERED.format(vendor=running.vendor, stage=named, why=why)
        raise UpstreamFailed(unanswered) from slow
    except UpstreamFailed as failed:
        cause = failed.__cause__
        if isinstance(cause, APIConnectionError | APITimeoutError):
            raise UpstreamFailed(
                UNANSWERED.format(vendor=running.vendor, stage=named, why=cause.message)
            ) from cause
        raise DeclarationRefused(
            REFUSED.format(vendor=running.vendor, stage=named, why=str(failed))
        ) from failed


async def _ask(stage: Modality, running: Running) -> None:
    try:
        if stage == "tts":
            await voices.sample(running, LINE)
        elif stage == "llm":
            await _answered(running)
        else:
            await _heard(running)
    except APIError as refused:
        raise UpstreamFailed(refused.message) from refused


async def _answered(running: Running) -> None:
    model = llm_of(running)
    context = ChatContext.empty()
    context.add_message(role="user", content=LINE)
    try:
        async with model.chat(chat_ctx=context, conn_options=ONCE) as stream:
            async for _ in stream:
                break
    finally:
        await model.aclose()


# Ears that only take a whole utterance have nothing to open: they are tried by the first call.
async def _heard(running: Running) -> None:
    ears = stt_of(running, None)
    try:
        if not ears.capabilities.streaming:
            return
        async with ears.stream(conn_options=ONCE) as stream:
            stream.push_frame(SILENCE)
            stream.end_input()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(LISTENED_S):
                    async for _ in stream:
                        continue
    finally:
        await ears.aclose()
