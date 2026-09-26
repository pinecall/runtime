"""A member is a person of one org with a role, and a role is a preset of key scopes."""

import pytest

from pinecall.types import KEY_SCOPES, ROLE_SCOPES, ROLES, DeclarationRefused, Member, parse_role

pytestmark = pytest.mark.unit


def a_member(**changed: object) -> Member:
    said: dict[str, object] = {
        "id": "m_1",
        "org": "clinica",
        "email": "berna@clinica.uy",
        "name": "Berna",
        "role": "developer",
    }
    said.update(changed)
    return Member(**said)  # pyright: ignore[reportArgumentType]


def test_every_role_presets_scopes_the_runtime_knows_and_admin_has_every_one() -> None:
    assert set(ROLE_SCOPES) == ROLES
    assert all(scopes <= KEY_SCOPES for scopes in ROLE_SCOPES.values())
    assert ROLE_SCOPES["admin"] == KEY_SCOPES


def test_the_roles_widen_the_way_the_floor_does() -> None:
    """qa reads; a supervisor also sits beside a live call; a manager also runs the org's tables."""
    assert ROLE_SCOPES["qa"] < ROLE_SCOPES["supervisor"] < ROLE_SCOPES["manager"]
    assert "app" in ROLE_SCOPES["developer"]
    assert "team" not in ROLE_SCOPES["developer"]
    assert "app" not in ROLE_SCOPES["manager"]


def test_a_members_scopes_are_their_roles_preset() -> None:
    assert a_member(role="qa").scopes == ROLE_SCOPES["qa"]
    assert a_member().status == "invited"
    assert a_member().agents == frozenset()


@pytest.mark.parametrize(
    ("changed", "why"),
    [
        ({"email": "berna"}, "one @ and a domain"),
        ({"email": "berna @clinica.uy"}, "one @ and a domain"),
        ({"name": "  "}, "has a name"),
        ({"role": "root"}, "a role is one of"),
        ({"status": "banned"}, "status is one of"),
        ({"id": ""}, "names their id"),
    ],
)
def test_a_member_refuses_what_is_not_one_in_a_sentence(
    changed: dict[str, object], why: str
) -> None:
    with pytest.raises(DeclarationRefused, match=why):
        a_member(**changed)


def test_a_role_is_read_off_a_word_and_a_word_that_is_none_is_refused_with_the_five() -> None:
    assert parse_role("supervisor") == "supervisor"
    with pytest.raises(DeclarationRefused, match=r"admin.*developer.*manager.*qa.*supervisor"):
        parse_role("owner")
