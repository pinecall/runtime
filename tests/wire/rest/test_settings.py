"""Tests for the bodies of the settings doors."""

from pinecall.wire.rest.settings import HistoryQuery, SettingsBody


def test_a_body_with_nothing_set_writes_nothing() -> None:
    assert SettingsBody().written() == {}


def test_a_history_asks_the_teams_scope_only_when_it_says_so() -> None:
    assert (HistoryQuery().team, HistoryQuery(team=True).limit) == (False, 20)
