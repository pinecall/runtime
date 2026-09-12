"""Two developers of one tenant, each holding the same agent in development, neither the other's."""

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import KeyRecord, held_by
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import DEVELOPMENT, PRODUCTION
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

# The one number the org shares in development, and the one it answers on in production.
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
    """The whole point: before this, the second `pinecall run` replaced the first."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=CARLA)

    bernas = registry.of(DEVELOPMENT, AGENT, BERNA)
    carlas = registry.of(DEVELOPMENT, AGENT, CARLA)
    assert bernas is not None and bernas.owner == BERNAS_SOCKET
    assert carlas is not None and carlas.owner == CARLAS_SOCKET


async def test_a_developer_holding_none_falls_back_to_the_orgs_own() -> None:
    """A development key that names nobody — CI's — holds the corner everybody else falls into."""
    registry = a_registry()
    await registry.register(CI, ORG, DEVELOPMENT, AGENT, [a_door("web")])

    held = registry.of(DEVELOPMENT, AGENT, BERNA)
    assert held is not None and held.owner == CI


async def test_their_own_wins_over_the_orgs_own() -> None:
    registry = a_registry()
    await registry.register(CI, ORG, DEVELOPMENT, AGENT, [a_door("web")])
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)

    held = registry.of(DEVELOPMENT, AGENT, BERNA)
    assert held is not None and held.owner == BERNAS_SOCKET
    assert registry.of(DEVELOPMENT, AGENT, CARLA) is not None
    assert registry.of(DEVELOPMENT, AGENT, CARLA).owner == CI  # pyright: ignore[reportOptionalMemberAccess]


async def test_production_has_one_corner_and_a_laptop_is_not_in_it() -> None:
    """A person's key holds no `app` in production, so the only holder there is the box's."""
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT, [a_door("phone", A_PROD_NUMBER)])
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)

    deployed = registry.of(PRODUCTION, AGENT)
    assert deployed is not None and deployed.owner == THE_BOX
    assert registry.at("phone", A_PROD_NUMBER) == deployed


async def test_the_development_number_is_the_orgs_and_starting_later_does_not_take_it() -> None:
    """Web and chat are each developer's; the number is shared, and it rings where it was claimed.

    Before the line, the second `pinecall run` silently took the first one's calls: Berna would
    dial the development number to test and it would answer in Carla's scrollback.
    """
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    # Not refused: it is the same agent in the same world, and a corner does not own a number.
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=CARLA
    )

    taking = registry.taking(DEVELOPMENT, AGENT)
    assert taking is not None and taking.owner == BERNAS_SOCKET
    assert registry.line_for(DEVELOPMENT, AGENT) == BERNA


async def test_a_developer_calling_from_their_own_phone_reaches_their_own_agent() -> None:
    """The point of it: no claim, no coordination, and three of them testing at once."""
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=CARLA
    )
    registry.calls_from(DEVELOPMENT, BERNAS_PHONE, BERNA)
    registry.calls_from(DEVELOPMENT, CARLAS_PHONE, CARLA)

    bernas = registry.taking(DEVELOPMENT, AGENT, BERNAS_PHONE)
    carlas = registry.taking(DEVELOPMENT, AGENT, CARLAS_PHONE)

    assert bernas is not None and bernas.owner == BERNAS_SOCKET
    assert carlas is not None and carlas.owner == CARLAS_SOCKET


async def test_a_number_nobody_claimed_falls_back_to_the_line() -> None:
    """A customer, a colleague's phone, a test from somewhere else: somebody still has to answer."""
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=CARLA
    )
    registry.calls_from(DEVELOPMENT, CARLAS_PHONE, CARLA)

    taking = registry.taking(DEVELOPMENT, AGENT, A_STRANGERS_PHONE)

    assert taking is not None and taking.owner == BERNAS_SOCKET, "Berna holds the line"


async def test_a_registered_number_whose_developer_is_not_running_this_agent_falls_back() -> None:
    """A setting made last week must not send a call to nobody: Carla is registered and away."""
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    registry.calls_from(DEVELOPMENT, CARLAS_PHONE, CARLA)

    taking = registry.taking(DEVELOPMENT, AGENT, CARLAS_PHONE)

    assert taking is not None and taking.owner == BERNAS_SOCKET


async def test_a_number_reaches_whatever_agent_that_developer_is_holding() -> None:
    """A phone is a person's, not an agent's: they say it once and it works on every agent."""
    registry = a_registry()
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, "otra-tienda", [a_door("web")], holder=CARLA
    )
    registry.calls_from(DEVELOPMENT, CARLAS_PHONE, CARLA)

    taking = registry.taking(DEVELOPMENT, "otra-tienda", CARLAS_PHONE)

    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_a_developer_stops_answering_their_own_calls_and_is_told_which_they_were() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)
    registry.calls_from(DEVELOPMENT, BERNAS_PHONE, BERNA)

    forgot = registry.forget_calls_from(DEVELOPMENT, BERNA)

    assert forgot == (BERNAS_PHONE,)
    assert registry.calling(DEVELOPMENT, BERNA) == ()


async def test_production_routes_by_nobodys_phone_because_it_has_one_corner() -> None:
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT, [a_door("phone", A_PROD_NUMBER)])

    taking = registry.taking(PRODUCTION, AGENT, BERNAS_PHONE)

    assert taking is not None and taking.owner == THE_BOX


async def test_the_second_developer_claims_the_line_and_then_it_is_theirs() -> None:
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=CARLA
    )

    took = registry.take_the_line(DEVELOPMENT, AGENT, CARLA)

    assert took.owner == CARLAS_SOCKET
    taking = registry.taking(DEVELOPMENT, AGENT)
    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_a_line_is_refused_to_somebody_holding_no_app_that_would_answer_it() -> None:
    """A ring lands on the line: handing it to a corner with no app in it would drop the call."""
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )

    with pytest.raises(Exception, match="is not held in development"):
        registry.take_the_line(DEVELOPMENT, AGENT, CARLA)
    assert registry.line_for(DEVELOPMENT, AGENT) == BERNA


async def test_a_console_is_never_handed_a_line_it_would_not_pick_up() -> None:
    """`pinecall chat` holds the agent and takes no call it did not open, so it claims nothing."""
    registry = a_registry()
    await registry.register(
        CARLAS_SOCKET,
        ORG,
        DEVELOPMENT,
        AGENT,
        [a_door("phone", A_DEV_NUMBER)],
        holder=CARLA,
        takes_unclaimed=False,
    )

    assert registry.has_a_line(DEVELOPMENT, AGENT) is False
    assert registry.taking(DEVELOPMENT, AGENT) is None


async def test_the_line_is_handed_on_when_the_terminal_holding_it_closes() -> None:
    """Not "the newest wins": it happens only when the corner that HAD the line went away."""
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=CARLA
    )

    await registry.release(BERNAS_SOCKET)

    assert registry.line_for(DEVELOPMENT, AGENT) == CARLA
    taking = registry.taking(DEVELOPMENT, AGENT)
    assert taking is not None and taking.owner == CARLAS_SOCKET


async def test_nobody_holds_the_line_once_the_last_terminal_closes() -> None:
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )

    await registry.release(BERNAS_SOCKET)

    assert registry.has_a_line(DEVELOPMENT, AGENT) is False
    assert registry.taking(DEVELOPMENT, AGENT) is None


async def test_who_else_could_take_it_is_every_other_corner_newest_first() -> None:
    """What the second developer's terminal prints, so a claim is a thing you can see to make."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=CARLA)

    waiting = registry.waiting_for_the_line(DEVELOPMENT, AGENT)

    assert [held.holder for held in waiting] == [CARLA, BERNA]


async def test_production_has_one_corner_and_it_is_the_line() -> None:
    """The concept costs production nothing: the box holds the only corner there is."""
    registry = a_registry()
    await registry.register(THE_BOX, ORG, PRODUCTION, AGENT, [a_door("phone", A_PROD_NUMBER)])

    assert registry.line_for(PRODUCTION, AGENT) is None
    assert registry.has_a_line(PRODUCTION, AGENT) is True
    taking = registry.taking(PRODUCTION, AGENT)
    assert taking is not None and taking.owner == THE_BOX


async def test_a_door_another_agent_holds_is_still_refused() -> None:
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    with pytest.raises(Exception, match="already answers for agent"):
        await registry.register(
            CARLAS_SOCKET,
            ORG,
            DEVELOPMENT,
            "otra-tienda",
            [a_door("phone", A_DEV_NUMBER)],
            holder=CARLA,
        )


async def test_a_listing_shows_one_row_per_slug_and_prefers_the_readers_own() -> None:
    """What a console draws and what the agent quota counts: a slug, not a process."""
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=CARLA)

    bernas = registry.holding(ORG, DEVELOPMENT, BERNA)
    assert [held.owner for held in bernas] == [BERNAS_SOCKET]
    carlas = registry.holding(ORG, DEVELOPMENT, CARLA)
    assert [held.owner for held in carlas] == [CARLAS_SOCKET]


async def test_one_leaving_leaves_the_other_holding() -> None:
    registry = a_registry()
    await registry.register(BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=BERNA)
    await registry.register(CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("web")], holder=CARLA)

    await registry.release(BERNAS_SOCKET)

    assert registry.of(DEVELOPMENT, AGENT, BERNA) is None
    assert registry.of(DEVELOPMENT, AGENT, CARLA) is not None


def test_the_corner_a_key_works_in_is_its_person_in_development_and_nobody_in_production() -> None:
    """The one rule, said once: auth/keys.py, and every door reads it from there."""
    laptop = KeyRecord(key_id="k_1", org=ORG, env=DEVELOPMENT, subject=BERNA, name="Berna")
    console = KeyRecord(key_id="k_2", org=ORG, env=PRODUCTION, subject=BERNA, name="Berna")
    machine = KeyRecord(key_id="k_3", org=ORG, env=DEVELOPMENT, label="ci")
    assert (held_by(laptop), held_by(console), held_by(machine)) == (BERNA, None, None)
