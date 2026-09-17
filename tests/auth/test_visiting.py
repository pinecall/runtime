"""A visitor's subject, and the keys table that asks whether its person still runs the box."""

import pytest

from pinecall.auth.keys import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.visiting import StandingKeys, a_visitor, the_operator, visiting
from pinecall.types import Member

pytestmark = pytest.mark.unit

BERNA = Member(
    id="m_berna",
    org="pinecall",
    email="bernardo@pinecall.io",
    name="Bernardo",
    role="admin",
    status="active",
    operator=True,
)


def test_a_visitors_subject_carries_the_folded_address_and_no_member_id_reads_as_one() -> None:
    assert a_visitor(" Bernardo@Pinecall.io ") == "operator:bernardo@pinecall.io"
    assert visiting("operator:bernardo@pinecall.io") == "bernardo@pinecall.io"
    assert visiting("m_berna") is None and visiting(None) is None


async def test_the_operator_is_an_active_flagged_row_of_that_address_in_any_org() -> None:
    twice = Member(id="m_2", org="clinica", email=BERNA.email, name="B", role="qa", status="active")
    members = MemoryMembers([twice, BERNA])
    assert await the_operator(members, "Bernardo@pinecall.io") == BERNA
    assert await the_operator(members, "ana@clinica.uy") is None
    await members.update(BERNA.org, BERNA.id, status="disabled")
    assert await the_operator(members, BERNA.email) is None


async def test_a_visitors_key_verifies_only_while_its_person_runs_the_box() -> None:
    members, table = MemoryMembers([BERNA]), MemoryKeys()
    keys = StandingKeys(table, members)
    visitor = await keys.issue("clinica", "operator · b", subject=a_visitor(BERNA.email))
    machine = await keys.issue("clinica", "prod server")

    assert await keys.verify(visitor.key) == visitor.record
    await members.make_operator(BERNA.org, BERNA.id, False)

    assert await keys.verify(visitor.key) is None
    assert await keys.verify(machine.key) == machine.record, "nobody else's key is asked anything"
    assert [row.revoked_at for row in await keys.listed("clinica")] == [None, None]
    assert await keys.revoke(next(iter(await keys.listed("clinica"))).fingerprint) is True
