"""Production asked who a person is: its rows back, its refusals whole, its silence a 502."""

import httpx
import pytest

from pinecall.auth.identity import (
    REDEEM,
    UNREACHABLE,
    UNREADABLE,
    Identity,
    NotRedeemed,
    Redeemed,
)
from pinecall.types import Member, Org

pytestmark = pytest.mark.unit

PRODUCTION_URL = "https://box.example.test"
TIENDA = Org(id="org_4ad9", slug="tienda", name="Tienda Sur")
BERNA = Member(
    id="m_berna", org=TIENDA.id, email="berna@tienda.uy", name="Berna", role="qa", status="active"
)


def answering(response: httpx.Response) -> Identity:
    """Production at PRODUCTION_URL, answering every redemption with this."""

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{PRODUCTION_URL}{REDEEM}"
        return response

    # The trailing slash an operator may type is not a second one in the path.
    return Identity(httpx.AsyncClient(transport=httpx.MockTransport(handle)), f"{PRODUCTION_URL}/")


async def test_what_production_answers_comes_back_as_the_two_rows_the_address_verified() -> None:
    answer = Redeemed.of(TIENDA, BERNA).model_dump()
    org, member = await answering(httpx.Response(200, json=answer)).redeem("lc_x")
    assert org == TIENDA
    assert (member.id, member.role, member.status, member.verified) == (
        "m_berna",
        "qa",
        "active",
        True,
    )


async def test_a_refusal_keeps_productions_status_and_sentence() -> None:
    refused = httpx.Response(403, json={"detail": "the person is no longer a member"})
    with pytest.raises(NotRedeemed) as said:
        await answering(refused).redeem("lc_x")
    assert (said.value.status, str(said.value)) == (403, "the person is no longer a member")


@pytest.mark.parametrize(
    ("answer", "sentence"),
    [
        (httpx.Response(500, text="boom"), UNREACHABLE),
        (httpx.Response(200, text="<html>a login page</html>"), UNREADABLE),
        (httpx.Response(200, json={"org": {"id": "o"}, "member": {}}), UNREADABLE),
    ],
)
async def test_anything_but_a_person_or_a_refusal_is_502_naming_where_it_asked(
    answer: httpx.Response, sentence: str
) -> None:
    with pytest.raises(NotRedeemed) as said:
        await answering(answer).redeem("lc_x")
    assert (said.value.status, str(said.value)) == (502, sentence.format(url=PRODUCTION_URL))
