"""A manager's key at the Team doors: it seats who it may, never an admin, never itself higher."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from pinecall.auth.grants import NOT_YOUR_OWN_ROW, NOT_YOURS_TO_GRANT
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.types import PRODUCTION, ROLE_SCOPES, SANDBOX, Member
from tests.api.conftest import A_KEY, A_RECORD
from tests.api.talking import at_the_console

pytestmark = pytest.mark.unit

MEMBERS = "/v1/members"

# Marta runs the floor: `team` among her scopes, production access, no `app`. The member doors are
# production's (api/accounts/identity.py), so a manager changes the team where she may act in
# production.
MARTA = Member(
    id="m_marta",
    org=A_RECORD.org,
    email="marta@clinica.uy",
    name="Marta",
    role="manager",
    status="active",
    production=True,
)
# Diego reads finished calls, and is who Marta tries to raise.
DIEGO = Member(
    id="m_diego",
    org=A_RECORD.org,
    email="diego@clinica.uy",
    name="Diego",
    role="qa",
    status="active",
)
A_MANAGERS_KEY = "pk_test_marta_runs_the_floor"
A_MANAGERS = KeyRecord(
    key_id="k_marta",
    org=A_RECORD.org,
    env=SANDBOX,
    scopes=ROLE_SCOPES["manager"],
    subject=MARTA.id,
    name=MARTA.name,
)
MANAGERS_OPEN = " · ".join(sorted(ROLE_SCOPES["manager"]))


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_MANAGERS_KEY: A_MANAGERS})


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers([MARTA, DIEGO])


@pytest.fixture
async def marta(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """Marta at production's console, saying the world she means."""
    async with at_the_console(A_MANAGERS_KEY, PRODUCTION) as http:
        yield http


def wanted(role: str, **more: Any) -> dict[str, Any]:
    return {"email": f"{role}@clinica.uy", "name": role.title(), "role": role, **more}


async def test_a_manager_invites_a_qa_and_is_refused_an_admin_and_a_developer(
    marta: httpx.AsyncClient,
) -> None:
    seated = await marta.post(MEMBERS, json=wanted("qa"))
    assert seated.status_code == 201, seated.text
    for above in ("admin", "developer"):
        refused = await marta.post(MEMBERS, json=wanted(above))
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == NOT_YOURS_TO_GRANT.format(
            role=above, opens=MANAGERS_OPEN
        )
    listed = (await marta.get(MEMBERS)).json()["members"]
    assert sorted(row["role"] for row in listed) == ["manager", "qa", "qa"], "nothing half-made"


# Production given only by somebody who acts there is tests/auth/test_grants.py's: at a door, a
# person with no production access is refused before the rule is asked (auth/env.py).
async def test_a_manager_may_not_raise_a_colleague_to_admin(
    marta: httpx.AsyncClient,
) -> None:
    raised = await marta.patch(f"{MEMBERS}/{DIEGO.id}", json={"role": "admin"})
    assert raised.status_code == 403
    assert raised.json()["detail"] == NOT_YOURS_TO_GRANT.format(role="admin", opens=MANAGERS_OPEN)
    # What she holds she hands out: a qa made supervisor is the floor's own business.
    within = await marta.patch(f"{MEMBERS}/{DIEGO.id}", json={"role": "supervisor"})
    assert within.status_code == 200, within.text
    assert within.json()["role"] == "supervisor" and within.json()["production"] is False


async def test_nobody_changes_their_own_role_or_switch(marta: httpx.AsyncClient) -> None:
    for body in ({"role": "admin"}, {"production": True}, {"role": "manager"}):
        own = await marta.patch(f"{MEMBERS}/{MARTA.id}", json=body)
        assert own.status_code == 409, own.text
        assert own.json()["detail"] == NOT_YOUR_OWN_ROW
    # The agents she works on are hers to narrow, and say nothing about what she may do.
    narrowed = await marta.patch(f"{MEMBERS}/{MARTA.id}", json={"agents": ["clinica-norte"]})
    assert narrowed.status_code == 200, narrowed.text


async def test_the_orgs_own_key_and_an_admins_still_seat_an_admin(
    tenant_http: httpx.AsyncClient,
) -> None:
    """A key naming nobody is the org's reach, not one person's: what every suite relied on."""
    seated = await tenant_http.post(MEMBERS, json=wanted("admin", production=True))
    assert seated.status_code == 201, seated.text
    assert seated.json()["member"]["production"] is True
