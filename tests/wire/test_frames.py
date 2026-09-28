"""Tests for the frames: the entry, the command, a whole log, reading and writing a model."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.frames import read_log
from pinecall.wire.parts import Contact
from tests.wire.golden import GOLDEN_LOG


def test_the_golden_log_reads_whole_in_the_order_it_was_written() -> None:
    entries = read_log(GOLDEN_LOG.read_text(encoding="utf-8"))
    assert entries
    seqs = [entry.seq for entry in entries]
    assert seqs == sorted(seqs), "the gaps are the ephemeral entries the store dropped"


def test_a_log_that_is_not_a_list_of_entries_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="log"):
        read_log("[{}]")


def test_a_model_is_read_or_refused_by_what_it_is_and_written_without_absent_fields() -> None:
    contact = Contact.read({"name": "Ana"}, "contact")
    assert contact.written() == {"name": "Ana"}
    with pytest.raises(DeclarationRefused, match="contact"):
        Contact.read({"nickname": "Ana"}, "contact")
