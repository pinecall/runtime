"""`pinecall-runtime orgs sso`: what an operator reads, and the one switch they may throw."""

from __future__ import annotations

import io

import httpx
import pytest

from pinecall.cli.operator import Operator
from pinecall.cli.orgs import sso as signing_in
from pinecall.orgs.sso import Sso
from pinecall.types import OrgSso
from tests.api.conftest import AN_ORG
from tests.api.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def wiring_a_provider(sso: Sso | None, http: httpx.AsyncClient) -> None:  # noqa: ARG001
    """The table the verb reads through the operator's door, and the client behind that door."""


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    """Where a verb writes, so a test reads the terminal instead of capturing a stream."""
    return io.StringIO()


async def _wired(sso: Sso, required: bool) -> None:
    await sso.put(
        OrgSso(
            org=AN_ORG.id,
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            domains=("tiendasur.uy",),
            role="qa",
            required=required,
        )
    )


async def test_an_org_that_signs_in_with_nobody_says_so_in_one_line(operator: Operator) -> None:
    out = printed()
    assert await signing_in.sso(AN_ORG.slug, False, operator, out) == 0
    assert out.getvalue().strip() == signing_in.NO_PROVIDER.format(org=AN_ORG.slug)


async def test_the_listing_names_the_issuer_the_client_and_who_it_seats(
    operator: Operator, sso: Sso
) -> None:
    """Never the secret: the CLI reads the same door the console does, and it answers none."""
    await _wired(sso, required=True)
    out = printed()
    assert await signing_in.sso(AN_ORG.slug, False, operator, out) == 0
    printed_lines = out.getvalue()
    assert ISSUER in printed_lines and CLIENT_ID in printed_lines
    assert "tiendasur.uy" in printed_lines and "qa" in printed_lines
    assert signing_in.THE_PROVIDER_ONLY in printed_lines
    assert CLIENT_SECRET not in printed_lines


async def test_off_gives_the_org_its_password_back_and_says_so(
    operator: Operator, sso: Sso
) -> None:
    """The break-glass: one flag, and the people of a locked-out org can log in again."""
    await _wired(sso, required=True)
    out = printed()
    assert await signing_in.sso(AN_ORG.slug, True, operator, out) == 0
    assert signing_in.A_PASSWORD_AGAIN in out.getvalue()
    kept = await sso.of(AN_ORG.id)
    assert kept is not None and kept.required is False
    # …and nothing else about the wiring moved, the secret included.
    assert kept.client_secret == CLIENT_SECRET and kept.role == "qa"
