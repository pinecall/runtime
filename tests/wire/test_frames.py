"""Tests for the frames: the entry, the command, a whole log, reading and writing a model."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.parts import Contact
from tests.wire.golden import golden_entries


def test_the_golden_log_reads_whole_in_the_order_it_was_written() -> None:
    entries = golden_entries()
    assert entries
    seqs = [entry.seq for entry in entries]
    assert seqs == sorted(seqs), "the gaps are the ephemeral entries the store dropped"


def test_a_model_is_read_or_refused_by_what_it_is_and_written_without_absent_fields() -> None:
    contact = Contact.read({"name": "Ana"}, "contact")
    assert contact.written() == {"name": "Ana"}
    with pytest.raises(DeclarationRefused, match="contact"):
        Contact.read({"nickname": "Ana"}, "contact")


def test_a_model_reads_from_json_text_and_writes_to_it_as_it_does_through_a_dict() -> None:
    contact = Contact.read_json('{"name": "Ana"}', "contact")
    assert contact == Contact.read({"name": "Ana"}, "contact")
    assert contact.written_json() == '{"name":"Ana"}'
    with pytest.raises(DeclarationRefused, match="contact"):
        Contact.read_json('{"nickname": "Ana"}', "contact")
    with pytest.raises(DeclarationRefused, match="contact"):
        Contact.read_json("not json", "contact")
