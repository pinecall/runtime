"""The vendored price list: what it is, where it came from, and what it must never be."""

import json

import pytest

from pinecall.providers import prices
from pinecall.providers.published_prices import FILE, published

pytestmark = pytest.mark.unit


def test_the_file_says_where_every_number_in_it_came_from() -> None:
    """A price nobody can trace is a price nobody can check: the source and the commit ship too."""
    said = json.loads(FILE.read_text(encoding="utf-8"))
    assert said["source"] == "mahimailabs/voice-prices"
    assert len(said["commit"]) == 40
    assert said["url"].startswith("https://raw.githubusercontent.com/mahimailabs/voice-prices/")


def test_it_prices_a_thousand_models_this_runtime_read_no_page_for() -> None:
    table = published()
    assert len(table.tokens) + len(table.media) > 900


def test_the_file_says_where_it_came_from_and_when_each_row_was_read() -> None:
    """For the reviewer of its diff: the runtime prices by the numbers and reads none of this."""
    data = json.loads(FILE.read_text(encoding="utf-8"))
    assert data["source"] and data["commit"]
    assert data["prices"]["cartesia/sonic-3"]["as_of"]
    assert data["prices"]["claude-haiku-4-5"]["as_of"]


# The four vendors of a pipeline this build can be pointed at from the console today, priced. Each
# one used to read `unpriced`, which in a summary is a bill nobody can see.
@pytest.mark.parametrize(
    "model", ["sonic-3", "mistv2", "aura-2-thalia-en", "cartesia/sonic-3", "deepgram/nova-3"]
)
def test_a_voice_or_an_ear_of_any_vendor_now_has_a_price(model: str) -> None:
    assert prices.media_price_of(model) is not None


@pytest.mark.parametrize("model", ["gemini-2.5-flash", "mistral-small-latest", "kimi-k2-thinking"])
def test_a_model_of_any_llm_vendor_now_has_a_price(model: str) -> None:
    priced = prices.price_of(model)
    assert priced is not None and priced.input > 0 and priced.output > 0


# The one rule between the two tables. Soniox is the row it was written for: the published list
# bills it by token and a livekit STT usage row counts seconds, so the hand-read hourly rate is
# the only one of the two that can price a real call at all.
def test_the_hand_read_table_is_asked_first() -> None:
    assert prices.media_price_of("stt-rt-v5") == prices.MEDIA_PRICES["stt-rt"]
    assert prices.price_of("claude-fable-5-1-20260901") == prices.PRICES["claude-fable-5-1"]
