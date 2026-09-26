"""An operator of the box opens any org from the console's switch, as themselves, with no seat."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.accounts.login import VISITS_PRODUCTION
from pinecall.api.accounts.org_switch import AS_THE_OPERATOR, NOT_THERE
from pinecall.auth.keys import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import ROLE_SCOPES, SANDBOX, Member
from tests.api.conftest import AN_ORG, over_the_asgi_app
from tests.api.talking import answering_in

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("wired")]

BERNA = Member(
    id="m_berna",
    org=AN_ORG.id,
    email="bernardo@pinecall.io",
    name="Bernardo",
    role="admin",
    status="active",
    operator=True,
)
ANA = Member(
    id="m_ana", org=AN_ORG.id, email="ana@clinica.uy", name="Ana", role="admin", status="active"
)
A_VISITOR = "operator:bernardo@pinecall.io"


@pytest.fixture
def members() -> MemoryMembers:
    """The clinic's two admins; the box made one of them an operator."""
    return MemoryMembers([BERNA, ANA])


@pytest.fixture
async def elsewhere(orgs: MemoryOrgs) -> str:
    """A tenant neither of them was ever invited to."""
    made = await orgs.create("tienda-sur", "Tienda Sur")
    assert made is not None
    return made.id


async def a_key_of(keys: MemoryKeys, member: Member, env: str = "production") -> httpx.AsyncClient:
    """A client knocking with a key minted for this member, as a login would have."""
    issued = await keys.issue(
        member.org,
        "console",
        env="sandbox" if env == "sandbox" else "production",
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
    )
    return over_the_asgi_app(f"Bearer {issued.key}")


async def walked_into(http: httpx.AsyncClient, org: str) -> dict[str, Any]:
    answer = await http.post("/v1/login/org", json={"org": org})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_an_operator_is_listed_every_org_and_a_member_only_their_own(
    keys: MemoryKeys, elsewhere: str
) -> None:
    operator, member = await a_key_of(keys, BERNA), await a_key_of(keys, ANA)

    every = (await operator.get("/v1/login/orgs")).json()["orgs"]
    own = (await member.get("/v1/login/orgs")).json()["orgs"]

    home = {
        "org": AN_ORG.id,
        "slug": AN_ORG.slug,
        "name": AN_ORG.name,
        "role": "admin",
        "status": "active",
        "here": True,
        "member": True,
    }
    assert own == [home]
    assert every[0] == home, "their own orgs first, oldest first, in the shape they always had"
    assert {
        "org": elsewhere,
        "slug": "tienda-sur",
        "name": "Tienda Sur",
        "role": AS_THE_OPERATOR,
        "status": "active",
        "here": False,
        "member": False,
    } in every
    assert all(not row["member"] for row in every[1:])
    await operator.aclose()
    await member.aclose()


async def test_walking_in_mints_an_admins_production_key_that_says_whose_it_is_and_seats_nobody(
    keys: MemoryKeys, members: MemoryMembers, elsewhere: str
) -> None:
    operator = await a_key_of(keys, BERNA, env="sandbox")

    issued = await walked_into(operator, "tienda-sur")

    assert {name: issued[name] for name in ("org", "env", "label", "subject", "name")} == {
        "org": elsewhere,
        "env": "production",
        "label": "operator · bernardo@pinecall.io",
        "subject": A_VISITOR,
        "name": "Bernardo",
    }
    assert issued["scopes"] == sorted(ROLE_SCOPES["admin"] - {"app"})
    assert "app" not in issued["scopes"], "the box looks and mends; it deploys nothing"
    assert await members.listed(elsewhere) == () and await members.seated(elsewhere) == 0
    # The tenant reads whose key it is on its own Keys screen, and may revoke it there.
    (row,) = await keys.listed(elsewhere)
    assert (row.label, row.subject) == ("operator · bernardo@pinecall.io", A_VISITOR)
    await operator.aclose()


async def test_a_visit_at_the_sandboxs_instance_is_minted_in_the_sandbox(
    keys: MemoryKeys, elsewhere: str, settings: Settings
) -> None:
    """An instance is one world: a visiting key of the other would open no door of this one."""
    answering_in(SANDBOX, settings)
    operator = await a_key_of(keys, BERNA, env="sandbox")
    issued = await walked_into(operator, "tienda-sur")
    assert (issued["org"], issued["env"]) == (elsewhere, "sandbox")
    await operator.aclose()


async def test_inside_whoami_names_them_and_the_switch_and_the_ops_doors_still_open(
    keys: MemoryKeys, elsewhere: str
) -> None:
    operator = await a_key_of(keys, BERNA)
    inside = over_the_asgi_app(f"Bearer {(await walked_into(operator, elsewhere))['key']}")

    whose = (await inside.get("/v1/whoami")).json()
    assert (whose["org"], whose["slug"], whose["subject"], whose["name"]) == (
        elsewhere,
        "tienda-sur",
        A_VISITOR,
        "Bernardo",
    )
    assert (whose["operator"], whose["visiting"]) == (True, True)
    # One name in every org: a visitor's address comes out of the subject, a member's off the row.
    assert whose["email"] == BERNA.email
    assert (await inside.get("/v1/members")).json() == {"members": []}, "an admin's reach"
    assert (await inside.get("/v1/ops/whoami")).json()["name"] == "Bernardo"
    listed = (await inside.get("/v1/login/orgs")).json()["orgs"]
    assert {row["slug"]: (row["here"], row["member"]) for row in listed} == {
        AN_ORG.slug: (False, True),
        "default": (False, False),
        "tienda-sur": (True, False),
    }
    # …and home again, as the member they are there.
    home = await walked_into(inside, AN_ORG.slug)
    assert (home["org"], home["subject"]) == (AN_ORG.id, BERNA.id)
    await operator.aclose()
    await inside.aclose()


async def test_at_home_whoami_says_operator_and_not_visiting_and_a_member_is_neither(
    keys: MemoryKeys,
) -> None:
    operator, member = await a_key_of(keys, BERNA), await a_key_of(keys, ANA)
    whose = (await operator.get("/v1/whoami")).json()
    assert (whose["operator"], whose["visiting"], whose["subject"]) == (True, False, BERNA.id)
    theirs = (await member.get("/v1/whoami")).json()
    assert (theirs["operator"], theirs["visiting"], theirs["email"]) == (False, False, ANA.email)
    assert whose["email"] == BERNA.email
    await operator.aclose()
    await member.aclose()


async def test_somebody_who_is_no_operator_is_let_into_no_org_but_their_own(
    keys: MemoryKeys, elsewhere: str
) -> None:
    member = await a_key_of(keys, ANA)
    refused = await member.post("/v1/login/org", json={"org": elsewhere})
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        NOT_THERE.format(org=elsewhere),
    )
    nowhere = await (await a_key_of(keys, BERNA)).post("/v1/login/org", json={"org": "nobody"})
    assert nowhere.status_code == 403, "an org that does not exist is nobody's, the operator's too"
    await member.aclose()


@pytest.mark.parametrize("taken_back", ["the flag", "disabled", "removed"])
async def test_a_visitors_key_stops_the_moment_they_stop_running_the_box(
    keys: MemoryKeys, members: MemoryMembers, elsewhere: str, taken_back: str
) -> None:
    operator = await a_key_of(keys, BERNA)
    inside = over_the_asgi_app(f"Bearer {(await walked_into(operator, elsewhere))['key']}")
    assert (await inside.get("/v1/whoami")).status_code == 200

    if taken_back == "the flag":
        await members.make_operator(AN_ORG.id, BERNA.id, False)
    elif taken_back == "disabled":
        await members.update(AN_ORG.id, BERNA.id, status="disabled")
    else:
        await members.remove(AN_ORG.id, BERNA.id)

    assert (await inside.get("/v1/whoami")).status_code == 401
    assert (await inside.get("/v1/ops/orgs")).status_code == 401
    assert (await inside.get("/v1/members")).status_code == 401
    await operator.aclose()
    await inside.aclose()


async def test_a_visitor_opens_no_sandbox_and_signs_no_terminal_in(
    keys: MemoryKeys, elsewhere: str
) -> None:
    operator = await a_key_of(keys, BERNA)
    inside = over_the_asgi_app(f"Bearer {(await walked_into(operator, elsewhere))['key']}")

    pairing = (await inside.post("/v1/login/pairings", json={"device": "laptop"})).json()
    approved = await inside.post(f"/v1/login/pairings/{pairing['code']}")
    assert (approved.status_code, approved.json()["detail"]) == (403, VISITS_PRODUCTION)
    await operator.aclose()
    await inside.aclose()


async def test_a_row_the_tenant_disabled_is_not_the_way_in_the_operator_walks_in_as_operator(
    keys: MemoryKeys, members: MemoryMembers, elsewhere: str
) -> None:
    invited = await members.invite(elsewhere, BERNA.email, "Bernardo", "qa", [])
    assert invited is not None
    await members.update(elsewhere, invited.member.id, status="disabled")
    operator = await a_key_of(keys, BERNA)

    issued = await walked_into(operator, elsewhere)

    assert issued["subject"] == A_VISITOR
    await operator.aclose()


async def test_the_flag_is_the_persons_so_their_key_in_any_org_of_theirs_opens_the_box(
    keys: MemoryKeys, members: MemoryMembers, elsewhere: str
) -> None:
    invited = await members.invite(elsewhere, BERNA.email, "Bernardo", "qa", [])
    assert invited is not None
    there = await members.update(elsewhere, invited.member.id, status="active")
    assert there is not None and there.operator is False, "the flag sits on the other row"
    as_a_member_there = await a_key_of(keys, there)

    assert (await as_a_member_there.get("/v1/ops/whoami")).status_code == 200
    assert (await as_a_member_there.get("/v1/whoami")).json()["operator"] is True
    assert (await (await a_key_of(keys, ANA)).get("/v1/ops/whoami")).status_code == 401
    await as_a_member_there.aclose()
