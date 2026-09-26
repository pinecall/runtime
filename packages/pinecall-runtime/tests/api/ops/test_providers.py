"""GET /v1/providers: every vendor this build runs, and what each one still wants on this box."""

import httpx
import pytest

from pinecall_testkit.plugins import without_the_plugin

pytestmark = pytest.mark.unit


async def test_the_door_answers_the_whole_catalogue(tenant_http: httpx.AsyncClient) -> None:
    """Forty-odd vendors and not five: the screen that offers a vendor offers all of them."""
    said = (await tenant_http.get("/v1/providers")).json()
    names = [row["name"] for row in said["providers"]]
    assert len(names) > 40
    assert {"anthropic", "elevenlabs", "soniox", "cartesia", "rime", "livekit"} <= set(names)
    assert said["defaults"] == {"llm": "anthropic", "stt": "deepgram", "tts": "cartesia"}
    assert "carolina" in said["voices"]


async def test_the_door_lists_the_models_this_build_vouches_for_per_vendor(
    tenant_http: httpx.AsyncClient,
) -> None:
    """A screen offers a list, the default first: a typed model name was a dead agent once."""
    models = (await tenant_http.get("/v1/providers")).json()["models"]
    assert models["llm/anthropic"][0] == "claude-haiku-4-5-20251001"
    assert models["stt/deepgram"][0] == "flux-general-multi"
    assert models["tts/elevenlabs"] == [
        "eleven_flash_v2_5",
        "eleven_v3_conversational",
        "eleven_multilingual_v2",
    ]
    assert models["tts/cartesia"] == ["sonic-3", "sonic-2"]
    assert "llm/cartesia" not in models


async def test_a_row_says_what_the_vendor_does_and_what_it_is_also_called(
    tenant_http: httpx.AsyncClient,
) -> None:
    said = (await tenant_http.get("/v1/providers")).json()
    rows = {row["name"]: row for row in said["providers"]}
    assert rows["elevenlabs"]["does"] == ["stt", "tts"]
    assert (rows["cartesia"]["voices_listed"], rows["elevenlabs"]["voices_listed"]) == (True, True)
    assert rows["rime"]["voices_listed"] is False, "its voice is a word typed in"
    assert "11labs" in rows["elevenlabs"]["aliases"]
    assert rows["anthropic"]["env"] == "ANTHROPIC_API_KEY"
    assert rows["anthropic"]["extra"] == "anthropic"


# One word for what a vendor is waiting for, decided in one place. Ring 0 holds a dead sentinel for
# the five tuned vendors and nothing for the rest, which is exactly the shape of a fresh box — so
# this asserts the difference is visible, never that a particular key is set.
async def test_a_vendor_the_box_has_no_key_for_says_so_without_saying_the_key(
    tenant_http: httpx.AsyncClient,
) -> None:
    answered = await tenant_http.get("/v1/providers")
    rows = {row["name"]: row for row in answered.json()["providers"]}
    assert (rows["anthropic"]["standing"], rows["anthropic"]["ready"]) == ("ready", True)
    assert (rows["rime"]["standing"], rows["rime"]["ready"]) == ("no key", False)
    assert "dead-sentinel" not in answered.text


async def test_a_vendor_whose_credentials_are_its_own_is_neither_ready_nor_wanting_a_key(
    tenant_http: httpx.AsyncClient,
) -> None:
    """AWS's chain and RTZR's client pair are not one string: a screen must send nobody looking
    for a key that does not exist, and must not call them ready either."""
    said = (await tenant_http.get("/v1/providers")).json()["providers"]
    rows = {row["name"]: row for row in said}
    for vendor in ("aws", "rtzr"):
        assert rows[vendor]["env"] is None
        assert rows[vendor]["ready"] is False
    assert rows["rtzr"]["standing"] == "its own"


@without_the_plugin("azure")
async def test_a_vendor_with_no_plugin_says_that_and_not_that_it_wants_a_key(
    tenant_http: httpx.AsyncClient,
) -> None:
    """`providers-big` is not installed in the suite, so Azure is the one with no plugin here."""
    said = (await tenant_http.get("/v1/providers")).json()["providers"]
    rows = {row["name"]: row for row in said}
    assert rows["azure"]["standing"] == "no plugin"
    assert rows["azure"]["extra"] == "azure"
