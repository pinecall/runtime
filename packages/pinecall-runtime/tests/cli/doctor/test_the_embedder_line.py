"""The doctor's embedder line: what this box embeds with, proved by embedding a word with it."""

import pytest

from pinecall.cli.doctor import verbs as doctor
from pinecall.cli.doctor.probes import Probes
from pinecall.providers.embedder import EmbedderUnreachable
from pinecall.settings import Settings, load_settings
from tests.cli.doctor.reading import probes_that_answer

pytestmark = pytest.mark.unit


def an_embedder_that_refuses(_settings: Settings) -> int:
    """What an embedder raises when the door is there and the answer is not a vector."""
    raise EmbedderUnreachable("TEI at http://127.0.0.1:8081 did not answer: HTTP 404")


def test_an_embedder_that_embeds_nothing_is_reported_and_stops_no_call() -> None:
    """A down embedder is printed, with why, and never makes the verdict: no call needs one."""
    embedder = _the_embedder(probes_that_answer(embed_width=an_embedder_that_refuses))
    assert not embedder.ok
    assert embedder.advisory
    assert "404" in embedder.detail
    assert "stops no call" in embedder.detail
    assert (
        doctor.first_failure(_the_report(probes_that_answer(embed_width=an_embedder_that_refuses)))
        is None
    )


def test_a_hub_whose_embedder_is_down_is_the_verdict_and_is_told_to_start_the_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hub answers the knowledge pushes: a shut door there is an outage the operator can fix."""
    monkeypatch.setenv("PINECALL_ROLE", "hub")
    results = _the_report(probes_that_answer(embed_width=an_embedder_that_refuses))
    down = doctor.first_failure(results)
    assert down is not None
    assert down.name == "embedder"
    assert not down.advisory
    assert "a knowledge push answers 503" in down.detail
    assert "systemctl start pinecall-tei" in down.detail
    assert "EMBED_PROVIDER" in down.detail


def test_a_hub_that_embeds_through_a_vendor_is_told_which_key_to_bring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix is a credential and not a container, so the sentence names the credential."""
    monkeypatch.setenv("PINECALL_ROLE", "hub")
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "")
    embedder = _the_embedder(probes_that_answer())
    assert not embedder.ok
    assert not embedder.advisory
    assert "no PERPLEXITY_API_KEY" in embedder.detail
    assert "make secret NAME=PERPLEXITY_API_KEY" in embedder.detail
    assert "systemctl" not in embedder.detail


def test_the_embedder_line_says_which_provider_and_model_this_box_embeds_with() -> None:
    embedder = _the_embedder(probes_that_answer())
    assert embedder.ok
    assert "tei · BAAI/bge-m3" in embedder.detail
    assert "http://127.0.0.1:8081" in embedder.detail
    assert "a word embedded, 1024 wide" in embedder.detail


def test_a_hosted_embedder_with_no_key_is_named_by_its_variable_and_never_knocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key is missing, so nothing is asked of the vendor: the fix is a variable, not a probe."""
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "")
    embedder = _the_embedder(probes_that_answer())
    assert not embedder.ok
    assert embedder.advisory
    assert "perplexity · pplx-embed-context-v1-4b" in embedder.detail
    assert "no PERPLEXITY_API_KEY" in embedder.detail


def test_a_hosted_embedder_is_proved_by_a_word_and_never_by_a_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key is what can be wrong here, and only a real request reads it."""
    monkeypatch.setenv("EMBED_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dead-sentinel")
    embedder = _the_embedder(probes_that_answer())
    assert embedder.ok
    assert "openrouter · perplexity/pplx-embed-v1-0.6b" in embedder.detail
    assert "https://openrouter.ai/api/v1" in embedder.detail


# The bug this check exists for: a vendor's base URL answers 404 for a live key, an expired one
# and none at all, so the box printed ✓ beside an embedder that could embed nothing.
def test_a_hosted_key_the_vendor_refuses_is_not_a_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "dead-sentinel")

    def refused(_settings: Settings) -> int:
        raise EmbedderUnreachable("Perplexity refused the key: HTTP 401")

    embedder = _the_embedder(probes_that_answer(embed_width=refused, http_status=lambda _url: 404))
    assert not embedder.ok
    assert "401" in embedder.detail


def test_an_embedder_of_another_width_writes_vectors_no_index_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model swapped in EMBED_MODEL answers a width the halfvec columns were not declared at."""
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-small")
    embedder = _the_embedder(probes_that_answer(embed_width=lambda _settings: 1536))
    assert not embedder.ok
    assert "1536 wide" in embedder.detail
    assert "halfvec(1024)" in embedder.detail


def _the_embedder(probes: Probes) -> doctor.Result:
    """The one line of the report this box's embedder gets, whichever provider it names."""
    return next(result for result in _the_report(probes) if result.name == "embedder")


def _the_report(probes: Probes) -> list[doctor.Result]:
    """Every check, against a stack where only what a test swapped is down."""
    return doctor.run_checks(load_settings(), probes)
