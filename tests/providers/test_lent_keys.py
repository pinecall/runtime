"""What the box lends an org: a vendor lends every model, `vendor/model` that model, by prefix."""

import pytest

from pinecall.providers.lent_keys import NotLent, a_lending, lent, refusal

pytestmark = pytest.mark.unit

# The free trial's lending, as the private package gives it: the harness and the cheap end only.
A_TRIAL = frozenset(
    {
        "deepgram",
        "cartesia",
        "anthropic/claude-haiku-4-5",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4-nano",
    }
)


def test_nothing_said_lends_everything_the_box_has() -> None:
    """None is every org nobody limited, and every self-hosted box."""
    assert lent(None, "anthropic", "claude-opus-5")
    assert lent(None, "deepgram", None)


def test_an_empty_lending_lends_nothing() -> None:
    assert not lent(frozenset(), "deepgram", None)
    assert not lent(frozenset(), "anthropic", "claude-haiku-4-5-20251001")


def test_a_whole_vendor_lends_every_model_of_it_and_its_default() -> None:
    assert lent(A_TRIAL, "deepgram", "flux-general-multi")
    assert lent(A_TRIAL, "cartesia", None)


# An id carries a date, so the model entry is read as a prefix — and a family's dear end is
# never lent by its cheap end's name.
def test_a_model_entry_lends_its_snapshots_and_nothing_dearer() -> None:
    assert lent(A_TRIAL, "anthropic", "claude-haiku-4-5-20251001")
    assert lent(A_TRIAL, "anthropic", "claude-haiku-4-5")
    assert not lent(A_TRIAL, "anthropic", "claude-sonnet-5")
    assert not lent(A_TRIAL, "anthropic", "claude-opus-5")
    assert lent(A_TRIAL, "openai", "gpt-5.4-nano")
    assert not lent(A_TRIAL, "openai", "gpt-5.4")
    assert not lent(A_TRIAL, "openai", "gpt-5-mini")


def test_a_model_entry_does_not_lend_the_vendors_unnamed_default() -> None:
    """None is "the vendor's own default": only a whole vendor is known to lend that."""
    assert not lent(A_TRIAL, "anthropic", None)


def test_a_vendor_is_read_by_any_of_its_words() -> None:
    assert lent(A_TRIAL, "claude", "claude-haiku-4-5-20251001")


def test_the_refusal_names_what_was_asked_what_may_run_and_the_other_way() -> None:
    said = refusal(A_TRIAL, "anthropic", "claude-sonnet-5")
    assert said.startswith("anthropic/claude-sonnet-5 is not lent to this org")
    assert "anthropic/claude-haiku-4-5" in said
    assert "pinecall providers add anthropic" in said
    assert "nothing of the box's" in refusal(frozenset(), "deepgram", None)


def test_a_door_takes_entries_spelled_once_and_refuses_what_names_nothing() -> None:
    assert a_lending(["Claude/claude-haiku-4-5", " deepgram "]) == frozenset(
        {"anthropic/claude-haiku-4-5", "deepgram"}
    )
    with pytest.raises(NotLent, match="no vendor"):
        a_lending(["zenith"])
    with pytest.raises(NotLent, match="no model"):
        a_lending(["anthropic/"])
