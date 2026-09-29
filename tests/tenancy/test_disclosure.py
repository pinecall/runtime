"""Tests for what a spoken call says first: the org's words, the platform's, none, the notice."""

from pinecall.tenancy.disclosure import disclosure_of, notice_of
from pinecall.wire.rest.accounts import OrgPolicy


def test_an_org_that_set_nothing_says_the_platforms_sentence_in_the_agents_language() -> None:
    assert disclosure_of(OrgPolicy(), "Clínica Norte", "es-AR") == (
        "Le habla un asistente automático en nombre de Clínica Norte."
    )
    assert notice_of(OrgPolicy(), "es") == "Esta llamada puede ser grabada."


def test_a_language_with_no_sentence_and_no_language_at_all_say_it_in_english() -> None:
    assert disclosure_of(OrgPolicy(), "Acme", "multi") == (
        "This is an automated assistant calling on behalf of Acme."
    )
    assert notice_of(OrgPolicy(), None) == "This call may be recorded."


def test_the_orgs_own_words_replace_the_platforms_and_an_empty_one_says_nothing() -> None:
    own = OrgPolicy(disclosure="Hi, this is Ana, Acme's virtual assistant.")
    assert disclosure_of(own, "Acme", "en") == "Hi, this is Ana, Acme's virtual assistant."
    assert disclosure_of(OrgPolicy(disclosure="  "), "Acme", "en") is None


def test_an_org_that_turned_the_notice_off_says_none() -> None:
    assert notice_of(OrgPolicy(recording_notice=False), "en") is None
