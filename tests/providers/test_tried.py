"""A stage a setting changes, tried once: the vendor's no refused, its silence told apart."""

import pytest

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.providers.build import Modality, Running
from pinecall.providers.tried import tried
from tests.fakes.acme import UNKNOWN


@pytest.mark.parametrize("stage", ["tts", "llm", "stt"])
async def test_a_stage_the_vendor_takes_passes(acme: str, stage: Modality) -> None:
    await tried(stage, Running(acme, "k"))


async def test_a_voice_the_vendor_does_not_have_is_refused_in_its_words(acme: str) -> None:
    with pytest.raises(DeclarationRefused, match=r"refused the voice this sets: .*no such voice"):
        await tried("tts", Running(acme, "k", voice=UNKNOWN))


async def test_a_model_the_vendor_does_not_have_is_refused(acme: str) -> None:
    with pytest.raises(DeclarationRefused, match="acme refused the model this sets"):
        await tried("llm", Running(acme, "k", model=UNKNOWN))


async def test_ears_the_vendor_does_not_have_are_refused(acme: str) -> None:
    with pytest.raises(DeclarationRefused, match="acme refused the ears this sets"):
        await tried("stt", Running(acme, "k", model=UNKNOWN))


async def test_a_vendor_that_does_not_answer_is_no_refusal(acme: str) -> None:
    with pytest.raises(UpstreamFailed, match=r"acme did not answer .* try again"):
        await tried("tts", Running(acme, "k", options={"refusal": "connection"}))


async def test_ears_that_take_only_a_whole_utterance_have_nothing_to_open(acme: str) -> None:
    await tried("stt", Running(acme, "k", model=UNKNOWN, options={"streams": False}))
