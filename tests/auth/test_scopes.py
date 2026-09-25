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
    settings = Settings(world="production", livekit_api_key=A_KEY, livekit_api_secret=A_SECRET)
    assert secret_for(settings) == LivekitKeys(A_KEY, A_SECRET)


# There was a second pair here, derived from PINECALL_DEV_KEY so a clone could sign its own reads.
# It opened no room: a gateway that looked like it worked and could not carry a call. The dev
# stack brings LiveKit up beside Postgres, so there is one pair and it is the real one.
def test_a_process_with_no_pair_verifies_nothing_rather_than_signing_what_opens_no_room() -> None:
    with pytest.raises(RuntimeError, match="call token"):
        secret_for(Settings(world="production", livekit_api_key=None, livekit_api_secret=None))
