"""The scope table, and where the pair that signs a participate token comes from."""

import pytest

from pinecall._settings import Settings
from pinecall.auth.scopes import (
    KEY_PROJECTION,
    PROJECTION_OF,
    LivekitKeys,
    secret_for,
)
from pinecall.types.token import SCOPES

pytestmark = pytest.mark.unit

A_KEY = "APIabc123"
A_SECRET = "a-secret-nobody-will-ever-deploy"


def test_every_scope_the_protocol_has_reads_through_exactly_one_projection() -> None:
    """A scope with no row would reach a sink that has to guess. None of them may."""
    assert set(PROJECTION_OF) == SCOPES
    assert PROJECTION_OF["participate"] != PROJECTION_OF["observe"]
    assert PROJECTION_OF["supervise"] == KEY_PROJECTION


def test_the_pair_is_livekits_own_because_the_token_is_a_livekit_token() -> None:
    """One token opens the room and reads the log, so there is only ever one pair to set."""
    settings = Settings(livekit_api_key=A_KEY, livekit_api_secret=A_SECRET, dev_key=None)
    assert secret_for(settings) == LivekitKeys(A_KEY, A_SECRET)


def test_a_clone_with_only_a_dev_key_still_signs_and_verifies_its_own_tokens() -> None:
    """The derived pair opens no real room, and says so by being derived from the dev key."""
    derived = a_clone_of("pk_dev")
    assert derived.api_secret != "pk_dev" and len(derived.api_secret) == 64
    assert derived == a_clone_of("pk_dev")
    assert derived != a_clone_of("pk_other")


def test_livekits_own_pair_wins_over_the_dev_key() -> None:
    """A box that has LiveKit configured mints tokens that open its rooms, not derived ones."""
    settings = Settings(livekit_api_key=A_KEY, livekit_api_secret=A_SECRET, dev_key="pk_dev")
    assert secret_for(settings) == LivekitKeys(A_KEY, A_SECRET)


def a_clone_of(dev_key: str) -> LivekitKeys:
    """A development box: a dev key and no LiveKit at all, whatever the shell happens to export."""
    return secret_for(Settings(livekit_api_key=None, livekit_api_secret=None, dev_key=dev_key))


def test_a_process_with_neither_verifies_nothing() -> None:
    with pytest.raises(RuntimeError, match="call token"):
        secret_for(Settings(livekit_api_key=None, livekit_api_secret=None, dev_key=None))
