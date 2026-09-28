"""A scope: the org, the world, the holder."""

from pinecall.domain.person import (
    owner_of,
)
from pinecall.domain.scope import THE_ORGS_OWN, Scope


def test_the_orgs_own_rows_have_an_empty_holder_because_null_never_matches_a_key() -> None:
    assert owner_of(None) == THE_ORGS_OWN == ""
    assert owner_of("m_carla") == "m_carla"


def test_a_corner_is_the_orgs_own_production_unless_said_otherwise() -> None:
    assert Scope("clinica") == Scope("clinica", "production", THE_ORGS_OWN)
    assert Scope("clinica", "sandbox", "m_berna").holder == "m_berna"
