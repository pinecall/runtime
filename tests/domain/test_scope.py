"""A scope: the org, the world, the holder."""

from pinecall.domain.scope import THE_ORGS_OWN, Scope


def test_a_corner_is_the_orgs_own_production_unless_said_otherwise() -> None:
    assert Scope("clinica") == Scope("clinica", "production", THE_ORGS_OWN)
    assert Scope("clinica", "sandbox", "m_berna").holder == "m_berna"
