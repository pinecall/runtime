"""`orgs invite | operator | remove-member`, against the real gateway over the ASGI transport."""

import io

import httpx
import pytest

from pinecall.auth.members_memory import MemoryMembers
from pinecall.cli.operator import Operator, OperatorRefused
from pinecall.cli.orgs.members import REMOVED, invite, make_operator, remove_member
from pinecall.cli.orgs.verbs import add_org

pytestmark = pytest.mark.unit


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    """Where a verb writes, so a test reads the terminal instead of capturing a stream."""
    return io.StringIO()


async def test_invite_prints_the_row_and_the_link_once_and_never_a_key(operator: Operator) -> None:
    """The operator hands a person a LINK: the console's card spends it for a password of theirs."""
    await add_org("tienda-sur", "Tienda Sur", operator, printed())
    out = printed()
    assert await invite("tienda-sur", "nico@tiendasur.uy", "Nico", "admin", operator, out) == 0
    row, link, sentence = out.getvalue().splitlines()
    assert (
        row.startswith("m_") and "nico@tiendasur.uy" in row and "admin" in row and "invited" in row
    )
    assert "/invitations/inv_" in link
    assert "once" in sentence and "week" in sentence
    assert "pk_" not in out.getvalue(), "an invitation is not a key and the verb prints none"


async def test_remove_member_finds_the_person_by_email_and_says_it_is_for_good(
    operator: Operator, members: MemoryMembers
) -> None:
    await add_org("tienda-sur", "Tienda Sur", operator, printed())
    await invite("tienda-sur", "nico@tiendasur.uy", "Nico", "developer", operator, printed())
    out = printed()
    assert await remove_member("tienda-sur", " Nico@TiendaSur.uy", operator, out) == 0
    row, sentence = out.getvalue().splitlines()
    assert row.startswith("m_") and "nico@tiendasur.uy" in row
    assert sentence.strip() == REMOVED
    assert await members.orgs_of("nico@tiendasur.uy") == ()


async def test_remove_member_names_a_stranger_and_carries_the_doors_refusal(
    operator: Operator, members: MemoryMembers, capsys: pytest.CaptureFixture[str]
) -> None:
    await add_org("tienda-sur", "Tienda Sur", operator, printed())
    assert await remove_member("tienda-sur", "nobody@tiendasur.uy", operator, printed()) == 1
    assert "no member of tienda-sur answers to nobody@tiendasur.uy" in capsys.readouterr().err
    # The org's only active admin stays: the door says so and the verb carries its sentence.
    await invite("tienda-sur", "nico@tiendasur.uy", "Nico", "admin", operator, printed())
    (nico,) = await members.orgs_of("nico@tiendasur.uy")
    await members.update(nico.org, nico.id, status="active")
    with pytest.raises(OperatorRefused, match="last active admin"):
        await remove_member("tienda-sur", "nico@tiendasur.uy", operator, printed())


async def test_operator_makes_and_unmakes_a_person_of_an_org(
    operator: Operator, members: MemoryMembers
) -> None:
    await add_org("tienda-sur", "Tienda Sur", operator, printed())
    await invite("tienda-sur", "nico@tiendasur.uy", "Nico", "admin", operator, printed())
    assert await make_operator("tienda-sur", "nico@tiendasur.uy", True, operator, printed()) == 0
    assert [one.operator for one in await members.orgs_of("nico@tiendasur.uy")] == [True]
    assert await make_operator("tienda-sur", "nico@tiendasur.uy", False, operator, printed()) == 0
    assert [one.operator for one in await members.orgs_of("nico@tiendasur.uy")] == [False]
