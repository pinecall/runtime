"""A key grants what it holds: a manager makes no admin, and nobody gives production they lack."""

from dataclasses import replace

import pytest

from pinecall.auth.granting import (
    NOT_YOURS_TO_GRANT,
    NOT_YOURS_TO_SWITCH,
    acts_in_production,
    cannot_grant,
    granting,
)
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.visiting import a_visitor
from pinecall.types import PRODUCTION, ROLE_SCOPES, SANDBOX, Member

pytestmark = pytest.mark.unit

MARTA = Member(id="m_marta", org="clinica", email="marta@clinica.uy", name="Marta", role="manager")
A_MANAGERS = KeyRecord(
    key_id="k_m", org="clinica", env=SANDBOX, scopes=ROLE_SCOPES["manager"], subject=MARTA.id
)
AN_ADMINS = KeyRecord(
    key_id="k_a", org="clinica", env=SANDBOX, scopes=ROLE_SCOPES["admin"], subject="m_laura"
)
A_SERVERS = KeyRecord(key_id="k_s", org="clinica", env=PRODUCTION)
A_VISITORS = KeyRecord(
    key_id="k_v",
    org="clinica",
    env=PRODUCTION,
    scopes=ROLE_SCOPES["admin"] - {"app"},
    subject=a_visitor("ops@pinecall.io"),
)


def test_a_role_is_granted_only_by_a_key_that_opens_everything_it_would() -> None:
    assert cannot_grant(A_MANAGERS, "qa") is None
    assert cannot_grant(A_MANAGERS, "supervisor") is None
    assert cannot_grant(A_MANAGERS, "manager") is None
    for above in ("admin", "developer"):
        said = cannot_grant(A_MANAGERS, above)
        assert said == NOT_YOURS_TO_GRANT.format(
            role=above, opens=" · ".join(sorted(ROLE_SCOPES["manager"]))
        )
    assert cannot_grant(AN_ADMINS, "admin") is None


def test_a_key_that_names_nobody_grants_what_it_is_asked_to() -> None:
    """A server's token, the box's own key, an operator's visit: not one person's reach."""
    assert cannot_grant(A_SERVERS, "admin") is None
    assert cannot_grant(A_VISITORS, "admin") is None, "the box may seat a tenant's first admin"


async def test_production_is_given_only_by_somebody_who_acts_there() -> None:
    members = MemoryMembers([replace(MARTA, status="active")])
    assert not await acts_in_production(A_MANAGERS, members)
    with pytest.raises(PermissionError) as refused:
        await granting(A_MANAGERS, members, "qa", True)
    assert str(refused.value) == NOT_YOURS_TO_SWITCH.format(name="this person")
    # The switch turned on for Marta, and the very next grant of hers goes through.
    await members.update(MARTA.org, MARTA.id, production=True)
    await granting(A_MANAGERS, members, "qa", True)
    # A production server's token acts there by what it is; a sandbox one does not.
    assert await acts_in_production(A_SERVERS, members)
    assert not await acts_in_production(
        KeyRecord(key_id="k_t", org="clinica", env=SANDBOX), members
    )


async def test_a_role_nobody_named_and_a_switch_left_off_are_not_asked_about() -> None:
    await granting(A_MANAGERS, MemoryMembers(), None, None)
    await granting(A_MANAGERS, MemoryMembers(), None, False)
