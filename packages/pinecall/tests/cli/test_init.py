"""`pinecall-runtime init`: what a person types once on a runtime nobody has used yet."""

import io

import httpx
import pytest

from pinecall.cli.init import run_init
from pinecall.cli.operator import Operator
from tests.api.conftest import (
    admission,
    embedder,
    fleet,
    graph,
    keys,
    knowledge,
    live,
    logs,
    lookups,
    memory,
    ops_http,
    orgs,
    registry,
    routes,
    settings,
    snapshots,
    store,
    threads,
    tokens,
    tuning,
    vault,
    wired,
)

pytestmark = pytest.mark.unit

__all__ = [
    "admission",
    "embedder",
    "fleet",
    "graph",
    "keys",
    "knowledge",
    "live",
    "logs",
    "lookups",
    "memory",
    "ops_http",
    "orgs",
    "registry",
    "routes",
    "settings",
    "snapshots",
    "store",
    "threads",
    "tokens",
    "tuning",
    "vault",
    "wired",
]


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    return io.StringIO()


# What replaced PINECALL_DEV_KEY. A clone used to come up on one magic key, one org and no
# tenants — a second runtime whose behaviour a box never had. Now it comes up on the same
# Postgres a box runs, and this is the one command that gives a person a way in.
async def test_it_makes_the_org_invites_the_person_and_hands_them_the_box(
    operator: Operator,
) -> None:
    out = printed()

    code = await run_init(
        "tienda-sur", "Tienda Sur", "nico@tiendasur.uy", "Nico", "admin", operator, out
    )

    org, member, link, *rest = out.getvalue().splitlines()
    assert code == 0
    assert org.startswith("org_") and "tienda-sur" in org
    assert "nico@tiendasur.uy" in member and "admin" in member
    # An operator of the BOX as well as an admin of their org: somebody has to be able to make the
    # second org, and on a fresh runtime there is nobody else who could be given that.
    assert "runs this box" in member
    assert "/invitations/inv_" in link
    assert "pinecall login" in "\n".join(rest), "and it says where to point a terminal"
    assert "pk_" not in out.getvalue(), "an invitation is not a key and this prints none"


async def test_running_it_twice_carries_on_to_the_person_rather_than_stopping_at_the_org(
    operator: Operator,
) -> None:
    """It is the verb somebody runs twice while reading the README."""
    await run_init("tienda-sur", None, "nico@tiendasur.uy", "Nico", "admin", operator, printed())
    out = printed()

    code = await run_init("tienda-sur", None, "ana@tiendasur.uy", "Ana", "admin", operator, out)

    assert code == 0
    assert out.getvalue().splitlines()[0] == "org tienda-sur is already there"
    assert "ana@tiendasur.uy" in out.getvalue()
