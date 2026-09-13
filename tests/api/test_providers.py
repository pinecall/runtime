"""GET /v1/providers: every vendor this build runs, and what each one still wants on this box."""

import httpx
import pytest

pytestmark = pytest.mark.unit


async def test_the_door_answers_the_whole_catalogue(tenant_http: httpx.AsyncClient) -> None:
    """Forty-odd vendors and not five: the screen that offers a vendor offers all of them."""
    said = (await tenant_http.get("/v1/providers")).json()
    names = [row["name"] for row in said["providers"]]
    assert len(names) > 40
    assert {"anthropic", "elevenlabs", "soniox", "cartesia", "rime", "livekit"} <= set(names)
    assert said["defaults"] == {"llm": "anthropic", "stt": "soniox", "tts": "elevenlabs"}
    assert "carolina" in said["voices"]


async def test_a_row_says_what_the_vendor_does_and_what_it_is_also_called(
    tenant_http: httpx.AsyncClient,
) -> None:
    said = (await tenant_http.get("/v1/providers")).json()
    rows = {row["name"]: row for row in said["providers"]}
    assert rows["elevenlabs"]["does"] == ["stt", "tts"]
    assert "11labs" in rows["elevenlabs"]["aliases"]
    assert rows["anthropic"]["env"] == "ANTHROPIC_API_KEY"
    assert rows["anthropic"]["extra"] == "anthropic"


# The two states a screen draws a vendor in: ready, or wanting something. Ring 0 holds a dead
# sentinel for the five tuned vendors and nothing for the rest, which is exactly the shape of a
# fresh box — so this asserts the difference is visible, never that a particular key is set.
async def test_a_vendor_the_box_has_no_key_for_says_so_without_saying_the_key(
    tenant_http: httpx.AsyncClient,
) -> None:
    answered = await tenant_http.get("/v1/providers")
    rows = {row["name"]: row for row in answered.json()["providers"]}
    assert rows["anthropic"]["keyed"] is True
    assert rows["rime"]["keyed"] is False
    assert "dead-sentinel" not in answered.text


async def test_a_vendor_that_needs_no_key_of_ours_is_never_shown_wanting_one(
    tenant_http: httpx.AsyncClient,
) -> None:
    """AWS runs on its own credential chain: a screen must not send anybody looking for a key."""
    rows = {
        row["name"]: row for row in (await tenant_http.get("/v1/providers")).json()["providers"]
    }
    assert rows["aws"]["env"] is None
    assert rows["aws"]["keyed"] is True
