"""Tests for what a call to a number goes through now: carrier, fence, world, agent."""

from dataclasses import replace

from pinecall.channels import routes
from pinecall.channels.routes import RouteWrite
from pinecall.channels.telephony import number_path, numbers
from pinecall.channels.telephony.numbers import NumberImport
from pinecall.domain.call import Route
from pinecall.tenancy import carriers
from tests.channels.conftest import A_NUMBER, PEER_NETWORK, Line, a_peer, approved, brought
from tests.conftest import postgres


async def recorded(line: Line, number: str = A_NUMBER) -> routes.RouteRecord:
    """The org's row at the number, with what the table keeps."""
    found = await routes.record_of(line.connections.pool, line.org, number)
    assert found is not None
    return found


@postgres
async def test_a_twilio_number_rings_when_its_agent_runs_and_is_broken_when_nobody_does(
    line: Line,
) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    found = await number_path.path_of(line.connections, await recorded(line), running=True)
    assert [(step.step, step.state) for step in found.steps] == [
        ("carrier", "ok"),
        ("fence", "ok"),
        ("world", "ok"),
        ("agent", "ok"),
    ]
    assert found.steps[0].says == "Twilio sends it to this box"
    nobody = await number_path.path_of(line.connections, await recorded(line), running=False)
    assert nobody.rings == "broken"
    assert nobody.steps[3].fix is not None


@postgres
async def test_a_number_twilio_no_longer_sends_here_says_so_and_how_to_fix_it(line: Line) -> None:
    await brought(line)
    line.twilio.owns(A_NUMBER)
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    sid, _ = line.twilio.numbers[A_NUMBER]
    line.twilio.numbers[A_NUMBER] = (sid, None)
    found = await number_path.path_of(line.connections, await recorded(line), running=True)
    assert (found.steps[0].state, found.rings) == ("broken", "broken")


@postgres
async def test_a_hooked_number_waits_for_its_first_call_and_a_call_settles_it(line: Line) -> None:
    await numbers.import_number(
        line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER, hooked=True)
    )
    (before,) = await number_path.rings_of(
        line.connections, [await recorded(line)], lambda _record: True
    )
    await routes.called(line.connections.pool, line.org, A_NUMBER)
    (after,) = await number_path.rings_of(
        line.connections, [await recorded(line)], lambda _record: True
    )
    assert (before, after) == ("waiting", "ok")


@postgres
async def test_a_peers_number_waits_on_the_operator_until_its_network_is_approved(
    line: Line,
) -> None:
    await carriers.put_carrier(line.connections.pool, line.connections.vault, line.org, a_peer())
    await numbers.import_number(line.connections, NumberImport(line.scope(), "recepcion", A_NUMBER))
    waiting = await number_path.path_of(line.connections, await recorded(line), running=True)
    assert (waiting.steps[1].state, waiting.steps[1].says) == (
        "waiting",
        f"{PEER_NETWORK} waits for the box's operator",
    )
    await approved(line, "pbx", PEER_NETWORK)
    admitted = await number_path.path_of(line.connections, await recorded(line), running=True)
    assert admitted.steps[1] == number_path.PathStep(
        "fence", "ok", "Admitted from your PBX's 1 networks"
    )


@postgres
async def test_a_number_another_orgs_older_row_answers_is_broken_at_the_fence(
    line: Line,
) -> None:
    async with line.connections.pool.connection() as connection:
        await connection.execute("insert into orgs (id, slug, name) values ('org_b', 'b', 'B')")
    ours = Route(org=line.org, agent="recepcion", channel="phone", number=A_NUMBER)
    await routes.put(line.connections.pool, replace(ours, org="org_b"), RouteWrite("typed"))
    await routes.put(line.connections.pool, ours, RouteWrite("typed"))
    found = await number_path.path_of(line.connections, await recorded(line), running=True)
    assert (found.steps[1].state, found.rings) == ("broken", "broken")
    assert "Another org" in found.steps[1].says
