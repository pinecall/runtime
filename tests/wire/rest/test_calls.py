"""Tests for the bodies of the call doors."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.rest.calls import AppendEntryRequest, MintTokenRequest


def test_a_token_asked_with_nothing_is_a_talk_token_reading_the_public_log() -> None:
    wanted = MintTokenRequest.read({}, "token")
    assert (wanted.scope, wanted.log, wanted.metadata) == ("talk", "public", {})


def test_an_entry_a_worker_appends_is_its_type_and_data_and_nothing_else() -> None:
    entry = AppendEntryRequest.read({"type": "turn.user", "data": {"text": "hola"}}, "entry")
    assert entry.written() == {"type": "turn.user", "data": {"text": "hola"}}
    with pytest.raises(DeclarationRefused, match="entry"):
        AppendEntryRequest.read({"type": "turn.user", "data": {}, "seq": 3}, "entry")
