"""Tests for people, roles and keys."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.person import (
    EVERY_SCOPE,
    KEY_SCOPES,
    ROLE_SCOPES,
    ROLES,
    Key,
    Member,
    key_scopes,
    parse_role,
)
from tests.domain.conftest import a_member


def test_the_roles_widen_the_way_the_floor_does() -> None:
    assert ROLE_SCOPES["qa"] < ROLE_SCOPES["supervisor"] < ROLE_SCOPES["manager"]
    assert "app" in ROLE_SCOPES["developer"]
    assert "team" not in ROLE_SCOPES["developer"]
    assert "app" not in ROLE_SCOPES["manager"]


def test_an_admin_opens_production_and_anybody_else_only_when_granted() -> None:
    assert a_member(role="admin").opens_production
    assert not a_member().opens_production
    assert Member("m_2", "clinica", "ana@clinica.uy", "Ana", "qa", production=True).opens_production


def test_a_role_is_read_off_a_word_and_a_word_that_is_none_is_refused_with_the_five() -> None:
    assert parse_role("supervisor") == "supervisor"
    with pytest.raises(DeclarationRefused, match=r"admin.*developer.*manager.*qa.*supervisor"):
        parse_role("owner")


def test_every_role_presets_scopes_the_runtime_knows_and_admin_has_every_one() -> None:
    assert set(ROLE_SCOPES) == set(ROLES)
    assert all(scopes <= KEY_SCOPES for scopes in ROLE_SCOPES.values())
    assert ROLE_SCOPES["admin"] == KEY_SCOPES


def test_a_members_scopes_are_their_roles_preset() -> None:
    assert a_member(role="qa").scopes == ROLE_SCOPES["qa"]
    assert a_member().status == "invited"
    assert a_member().agents == frozenset()


def test_the_fleet_scope_is_never_a_default_and_the_words_are_read_or_refused() -> None:
    assert "fleet" in EVERY_SCOPE
    assert "fleet" not in KEY_SCOPES
    assert key_scopes(["calls", "app", "calls"]) == frozenset({"app", "calls"})
    with pytest.raises(DeclarationRefused, match="'root' is not a key scope"):
        key_scopes(["calls", "root"])


def test_a_key_opens_production_with_every_scope_but_fleet_unless_said() -> None:
    key = Key("k_1", "clinica")
    assert (key.env, key.scopes, key.expires_at) == ("production", KEY_SCOPES, None)
    assert Key("k_2", "clinica", env="sandbox", scopes=frozenset({"app"})).scopes == {"app"}
