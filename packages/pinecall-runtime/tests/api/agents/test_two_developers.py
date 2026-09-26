"""Two developers of one tenant, each holding the same agent in sandbox, neither the other's."""

import pytest

from pinecall.api.agents.registry_reads import developers_sandbox_copy
from pinecall.auth.keys import KeyRecord, is_held_by, is_operator_key
from pinecall.live.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION, ROLE_SCOPES, SANDBOX
from pinecall.types.channel import Channel
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

ORG = "acme"
AGENT = "tienda-sur"

BERNAS_SOCKET = "app_bernas_laptop"
CARLAS_SOCKET = "app_carlas_laptop"
CI = "app_the_ci_job"
THE_BOX = "app_the_prod_server"

BERNA = "m_berna"
CARLA = "m_carla"

# The one number the org shares in sandbox, and the one it answers on in production.
A_DEV_NUMBER = "+59829001199"
A_PROD_NUMBER = "+59829001122"

# The phones the two of them call FROM. Saying so once is what makes the claim unnecessary.
BERNAS_PHONE = "+59899111111"
CARLAS_PHONE = "+59899222222"
A_STRANGERS_PHONE = "+59899333333"


def a_door(channel: Channel, number: str | None = None) -> defs.Route:
    return defs.Route(channel=channel, number=number)


def a_registry() -> Registry:
    return Registry(Logs(MemoryStore()))


async def test_each_developer_holds_their_own_and_neither_takes_the_others() -> None:
    """The whole point: before this, the second `pinecall start` replaced the first."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    bernas = registry.of(SANDBOX, AGENT, BERNA)
    carlas = registry.of(SANDBOX, AGENT, CARLA)
    assert bernas is not None and bernas.owner == BERNAS_SOCKET
    assert carlas is not None and carlas.owner == CARLAS_SOCKET


async def test_a_developer_holding_none_falls_back_to_the_orgs_own() -> None:
    """A sandbox key that names nobody — CI's — holds the corner everybody else falls into."""
    registry = a_registry()
    await registry.register(CI, ORG, SANDBOX, AGENT)

    held = registry.of(SANDBOX, AGENT, BERNA)
    assert held is not None and held.owner == CI


async def test_their_own_wins_over_the_orgs_own() -> None:
    registry = a_registry()
    await registry.register(CI, ORG, SANDBOX, AGENT)
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)

    held = registry.of(SANDBOX, AGENT, BERNA)
    assert held is not None and held.owner == BERNAS_SOCKET
    assert registry.of(SANDBOX, AGENT, CARLA) is not None
    assert registry.of(SANDBOX, AGENT, CARLA).owner == CI  # pyright: ignore[reportOptionalMemberAccess]


async def test_production_has_one_corner_and_a_laptop_is_not_in_it() -> None:
    """A person's key holds no `app` in production, so the only holder there is the box's."""
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT)
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)

    deployed = registry.of(PRODUCTION, AGENT)
    assert deployed is not None and deployed.owner == THE_BOX
    assert registry.of(SANDBOX, AGENT, BERNA) is not None


async def test_the_sandbox_number_is_the_orgs_and_starting_later_does_not_take_it() -> None:
    """Web and chat are each developer's; the number is shared, and it rings where it was claimed.

    Before the line, the second `pinecall start` silently took the first one's calls: Berna would
    dial the sandbox number to test and it would answer in Carla's scrollback.
    """
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    # Not refused: it is the same agent in the same world, and a corner does not own a number.
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    taking = registry.taking(SANDBOX, AGENT)
    assert taking is not None and taking.owner == BERNAS_SOCKET
    assert registry.line_for(SANDBOX, AGENT) == BERNA


async def test_a_developer_calling_from_their_own_phone_reaches_their_own_agent() -> None:
    """The point of it: no claim, no coordination, and three of them testing at once."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)
    registry.calls_from(SANDBOX, BERNAS_PHONE, BERNA)
    registry.calls_from(SANDBOX, CARLAS_PHONE, CARLA)

    bernas = registry.taking(SANDBOX, AGENT, BERNAS_PHONE)
    carlas = registry.taking(SANDBOX, AGENT, CARLAS_PHONE)

    assert bernas is not None and bernas.owner == BERNAS_SOCKET
    assert carlas is not None and carlas.owner == CARLAS_SOCKET


async def test_a_number_nobody_claimed_falls_back_to_the_line() -> None:
    """A customer, a colleague's phone, a test from somewhere else: somebody still has to answer."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)
    registry.calls_from(SANDBOX, CARLAS_PHONE, CARLA)

    taking = registry.taking(SANDBOX, AGENT, A_STRANGERS_PHONE)

    assert taking is not None and taking.owner == BERNAS_SOCKET, "Berna holds the line"


async def test_a_registered_number_whose_developer_is_not_running_this_agent_falls_back() -> None:
    """A setting made last week must not send a call to nobody: Carla is registered and away."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    registry.calls_from(SANDBOX, CARLAS_PHONE, CARLA)

    taking = registry.taking(SANDBOX, AGENT, CARLAS_PHONE)

    assert taking is not None and taking.owner == BERNAS_SOCKET


async def test_a_number_reaches_whatever_agent_that_developer_is_holding() -> None:
    """A phone is a person's, not an agent's: they say it once and it works on every agent."""
    registry = a_registry()
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, "otra-tienda", holder=CARLA)
    registry.calls_from(SANDBOX, CARLAS_PHONE, CARLA)

    taking = registry.taking(SANDBOX, "otra-tienda", CARLAS_PHONE)

    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_a_developer_stops_answering_their_own_calls_and_is_told_which_they_were() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    registry.calls_from(SANDBOX, BERNAS_PHONE, BERNA)

    forgot = registry.forget_calls_from(SANDBOX, BERNA)

    assert forgot == (BERNAS_PHONE,)
    assert registry.calling(SANDBOX, BERNA) == ()


async def test_production_routes_by_nobodys_phone_because_it_has_one_corner() -> None:
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT)

    taking = registry.taking(PRODUCTION, AGENT, BERNAS_PHONE)

    assert taking is not None and taking.owner == THE_BOX


async def test_the_second_developer_claims_the_line_and_then_it_is_theirs() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    took = registry.take_the_line(SANDBOX, AGENT, CARLA)

    assert took.owner == CARLAS_SOCKET
    taking = registry.taking(SANDBOX, AGENT)
    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_a_line_is_refused_to_somebody_holding_no_app_that_would_answer_it() -> None:
    """A ring lands on the line: handing it to a corner with no app in it would drop the call."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)

    with pytest.raises(Exception, match="is not held in sandbox"):
        registry.take_the_line(SANDBOX, AGENT, CARLA)
    assert registry.line_for(SANDBOX, AGENT) == BERNA


async def test_a_console_is_never_handed_a_line_it_would_not_pick_up() -> None:
    """`pinecall chat` holds the agent and takes no call it did not open, so it claims nothing."""
    registry = a_registry()
    await registry.register(
        CARLAS_SOCKET,
        ORG,
        SANDBOX,
        AGENT,
        holder=CARLA,
        takes_unclaimed=False,
    )

    assert registry.has_a_line(SANDBOX, AGENT) is False
    assert registry.taking(SANDBOX, AGENT) is None


async def test_the_line_is_handed_on_when_the_terminal_holding_it_closes() -> None:
    """Not "the newest wins": it happens only when the corner that HAD the line went away."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    await registry.release(BERNAS_SOCKET)

    assert registry.line_for(SANDBOX, AGENT) == CARLA
    taking = registry.taking(SANDBOX, AGENT)
    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_nobody_holds_the_line_once_the_last_terminal_closes() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)

    await registry.release(BERNAS_SOCKET)

    assert registry.has_a_line(SANDBOX, AGENT) is False
    assert registry.taking(SANDBOX, AGENT) is None


async def test_who_else_could_take_it_is_every_other_corner_newest_first() -> None:
    """What the second developer's terminal prints, so a claim is a thing you can see to make."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    waiting = registry.waiting_for_the_line(SANDBOX, AGENT)

    assert [held.holder for held in waiting] == [CARLA, BERNA]


async def test_production_has_one_corner_and_it_is_the_line() -> None:
    """The concept costs production nothing: the box holds the only corner there is."""
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT)

    assert registry.line_for(PRODUCTION, AGENT) is None
    assert registry.has_a_line(PRODUCTION, AGENT) is True
    taking = registry.taking(PRODUCTION, AGENT)
    assert taking is not None and taking.owner == THE_BOX


async def test_a_listing_shows_one_row_per_slug_and_prefers_the_readers_own() -> None:
    """What a console draws and what the agent quota counts: a slug, not a process."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    bernas = registry.holding(ORG, SANDBOX, BERNA)
    assert [held.owner for held in bernas] == [BERNAS_SOCKET]
    carlas = registry.holding(ORG, SANDBOX, CARLA)
    assert [held.owner for held in carlas] == [CARLAS_SOCKET]


async def test_one_leaving_leaves_the_other_holding() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)

    await registry.release(BERNAS_SOCKET)

    assert registry.of(SANDBOX, AGENT, BERNA) is None
    assert registry.of(SANDBOX, AGENT, CARLA) is not None


def test_the_corner_a_key_works_in_is_its_person_in_sandbox_and_nobody_in_production() -> None:
    """The one rule, said once: auth/keys.py, and every door reads it from there."""
    laptop = KeyRecord(key_id="k_1", org=ORG, env=SANDBOX, subject=BERNA, name="Berna")
    console = KeyRecord(key_id="k_2", org=ORG, env=PRODUCTION, subject=BERNA, name="Berna")
    machine = KeyRecord(key_id="k_3", org=ORG, env=SANDBOX, label="ci")
    assert (is_held_by(laptop), is_held_by(console), is_held_by(machine)) == (BERNA, None, None)


async def test_a_reader_who_sees_the_team_gets_one_row_per_corner_saying_whose() -> None:
    """The admin's page: collapsing the two would hide the very thing they opened it for."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, SANDBOX, AGENT, holder=CARLA)
    await registry.register(CI, ORG, SANDBOX, AGENT)

    every = registry.holding(ORG, SANDBOX, BERNA, every_corner=True)

    assert sorted(held.holder or "the org's" for held in every) == [BERNA, CARLA, "the org's"]
    assert {held.slug for held in every} == {AGENT}


def test_who_sees_every_corner_opens_the_team_and_holds_an_agent() -> None:
    """An admin opens `team` and `app`; a manager opens `team` alone and a developer `app` alone,
    and a developer's sandbox is neither the floor's nor a colleague's to open."""
    admin = KeyRecord(key_id="k_1", org=ORG, env=SANDBOX, scopes=ROLE_SCOPES["admin"])
    manager = KeyRecord(key_id="k_2", org=ORG, env=SANDBOX, scopes=ROLE_SCOPES["manager"])
    developer = KeyRecord(key_id="k_3", org=ORG, env=SANDBOX, scopes=ROLE_SCOPES["developer"])

    seen = (is_operator_key(admin), is_operator_key(manager), is_operator_key(developer))

    assert seen == (True, False, False)


async def test_a_developers_phone_dialling_the_production_number_reaches_their_copy() -> None:
    """Testing on the line customers use: Berna's phone reaches Berna's laptop, everybody else
    reaches the box — the same number, dialled from two phones."""
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT)
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, AGENT, holder=BERNA)
    registry.calls_from(SANDBOX, BERNAS_PHONE, BERNA)

    assert developers_sandbox_copy(registry, ORG, AGENT, BERNAS_PHONE) == BERNA
    assert developers_sandbox_copy(registry, ORG, AGENT, A_STRANGERS_PHONE) is None


async def test_a_developer_not_holding_the_agent_leaves_their_own_calls_in_production() -> None:
    """A phone said to be theirs last week, and nothing running today: production answers."""
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT)
    await registry.register(BERNAS_SOCKET, ORG, SANDBOX, "otro-agente", holder=BERNA)
    registry.calls_from(SANDBOX, BERNAS_PHONE, BERNA)

    assert developers_sandbox_copy(registry, ORG, AGENT, BERNAS_PHONE) is None


async def test_a_developer_of_another_org_never_takes_this_orgs_production_calls() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, "otra-org", SANDBOX, AGENT, holder=BERNA)
    registry.calls_from(SANDBOX, BERNAS_PHONE, BERNA)

    assert developers_sandbox_copy(registry, ORG, AGENT, BERNAS_PHONE) is None
