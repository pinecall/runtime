"""`pinecall-runtime routes`, verb by verb, against the real gateway over the ASGI transport."""

import io
import json
from pathlib import Path

import httpx
import pytest

from pinecall.cli.operator import Operator, OperatorRefused
from pinecall.cli.routes.verbs import (
    OPS_ROUTES,
    add_route,
    list_routes,
    remove_route,
    seed_routes,
)
from pinecall.live.registry import Registry
from pinecall.types import PRODUCTION
from tests.api.conftest import A_RECORD

pytestmark = pytest.mark.unit

ORG = A_RECORD.org
NUMBER = "+59829000000"
AN_OWNER = "app_the_routes_verbs"
CLINICA = "clinica-norte"
TIENDA = "tienda-sur"


@pytest.fixture
def operator(ops_http: httpx.AsyncClient) -> Operator:
    """The CLI's own client, on the gateway this test is running in the same loop."""
    return Operator(ops_http)


def printed() -> io.StringIO:
    """Where a verb writes, so a test reads the terminal instead of capturing a stream."""
    return io.StringIO()


async def test_list_says_so_when_the_org_answers_nothing_at_all(operator: Operator) -> None:
    """An empty screen is not an answer: a fresh box says which org it looked in."""
    out = printed()
    assert await list_routes(ORG, operator, out) == 0
    assert out.getvalue().strip() == f"no routes in org {ORG} in production"


async def test_add_prints_the_door_it_wrote(operator: Operator) -> None:
    out = printed()
    assert await add_route(ORG, NUMBER, TIENDA, "phone", operator, out) == 0
    assert f"{NUMBER} phone → {TIENDA}" in out.getvalue()


# One number, one row, and `add` over it is a move: there is no second table to take it from and
# no app to lose it, because a class declares no doors.
async def test_add_over_a_number_that_answers_somewhere_moves_it(operator: Operator) -> None:
    await add_route(ORG, NUMBER, CLINICA, "phone", operator, printed())
    await add_route(ORG, NUMBER, TIENDA, "phone", operator, printed())
    out = printed()
    await list_routes(ORG, operator, out)
    assert [line.split() for line in out.getvalue().splitlines()] == [[NUMBER, "phone", TIENDA]]


async def test_list_shows_every_row_of_the_org(operator: Operator, registry: Registry) -> None:
    """An agent an app is holding adds nothing here: holding an agent types no door."""
    await registry.register(AN_OWNER, ORG, PRODUCTION, CLINICA)
    await add_route(ORG, NUMBER, TIENDA, "phone", operator, printed())
    out = printed()
    await list_routes(ORG, operator, out)
    rows = [line.split() for line in out.getvalue().splitlines()]
    assert rows == [[NUMBER, "phone", TIENDA]]


async def test_rm_takes_the_number_back_and_a_number_nobody_typed_is_a_refusal(
    operator: Operator,
) -> None:
    """`routes rm` on a typo must not read as done, so the refusal reaches the exit code."""
    await add_route(ORG, NUMBER, TIENDA, "phone", operator, printed())
    out = printed()
    assert await remove_route(ORG, NUMBER, operator, out) == 0
    assert f"{NUMBER} removed" in out.getvalue()
    with pytest.raises(OperatorRefused, match="404"):
        await remove_route(ORG, NUMBER, operator, printed())


async def test_seed_applies_every_route_in_the_file_and_a_missing_file_is_an_error(
    operator: Operator, tmp_path: Path
) -> None:
    """What a fresh box comes up answering, and what it says when the file is not there."""
    seed = tmp_path / "routes.json"
    seed.write_text(
        json.dumps(
            [
                {"org": ORG, "number": NUMBER, "agent": CLINICA, "channel": "phone"},
                {"org": ORG, "number": "+59829000001", "agent": CLINICA},
            ]
        ),
        encoding="utf-8",
    )
    assert await seed_routes(seed, operator, printed()) == 0
    listed = await operator.get(OPS_ROUTES, org=ORG)
    # The second row named no channel: a number answers the phone unless somebody says else.
    assert [route["channel"] for route in listed] == ["phone", "phone"]

    out = printed()
    assert await seed_routes(tmp_path / "nowhere.json", operator, out) == 1
    assert "no such file" in out.getvalue()
