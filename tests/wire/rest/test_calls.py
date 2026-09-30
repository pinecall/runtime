"""Tests for the bodies of the call doors."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import JsonObject
from pinecall.wire.rest.calls import AppendEntriesRequest, AppendEntryRequest, MintTokenRequest


def test_a_token_asked_with_nothing_is_a_talk_token_reading_the_public_log() -> None:
    wanted = MintTokenRequest.read({}, "token")
    assert (wanted.scope, wanted.log, wanted.metadata) == ("talk", "public", {})


def test_an_entry_a_worker_appends_is_its_type_and_data_and_nothing_else() -> None:
    entry = AppendEntryRequest.read({"type": "turn.user", "data": {"text": "hola"}}, "entry")
    assert entry.written() == {"type": "turn.user", "data": {"text": "hola"}}
    with pytest.raises(DeclarationRefused, match="entry"):
        AppendEntryRequest.read({"type": "turn.user", "data": {}, "seq": 3}, "entry")


def test_a_batch_says_how_many_came_before_it_and_when_each_entry_happened() -> None:
    body: JsonObject = {"after": 2, "entries": [{"type": "turn.user", "data": {}, "ts": 4.5}]}
    batch = AppendEntriesRequest.read(body, "batch")
    assert (batch.after, [(item.type, item.ts) for item in batch.entries]) == (
        2,
        [("turn.user", 4.5)],
    )
    with pytest.raises(DeclarationRefused, match="batch"):
        AppendEntriesRequest.read({**body, "after": -1}, "batch")
    with pytest.raises(DeclarationRefused, match="batch"):
        AppendEntriesRequest.read(
            {"after": 0, "entries": [{"type": "turn.user", "data": {}}]}, "batch"
        )
