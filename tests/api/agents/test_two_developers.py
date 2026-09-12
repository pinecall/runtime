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


async def test_the_development_number_is_the_orgs_and_the_newest_run_answers_it() -> None:
    """Web and chat are each developer's; a number is one door in the world, and it is shared."""
    registry = a_registry()
    await registry.register(
        BERNAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=BERNA
    )
    # Not refused: it is the same agent in the same world, and a corner does not own a number.
    await registry.register(
        CARLAS_SOCKET, ORG, DEVELOPMENT, AGENT, [a_door("phone", A_DEV_NUMBER)], holder=CARLA
    )

    answering = registry.at("phone", A_DEV_NUMBER)
    assert answering is not None and answering.owner == CARLAS_SOCKET
    taking = registry.taking(DEVELOPMENT, AGENT)
    assert taking is not None and taking.owner == CARLAS_SOCKET


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
