"""Which route answers a WhatsApp number when the agent declares more than one door on it."""

from __future__ import annotations

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.routes.table import MemoryRoutes
from pinecall.whatsapp.routing import answering
from pinecall_protocol import defs
from tests.api.conftest import A_RECORD, AGENT
from tests.api.whatsapp.conftest import AN_APP, THE_CLINICS_NUMBER

pytestmark = pytest.mark.unit


# The clinic's own declaration: phone first, WhatsApp second, both on the one number the clinic
# has. The first live signed body found the phone route answering a WhatsApp message.
async def test_an_agent_with_a_phone_and_a_whatsapp_on_one_number_answers_whatsapp_by_the_door(
    registry: Registry, routes: MemoryRoutes
) -> None:
    await registry.register(
        AN_APP,
        A_RECORD.org,
        AGENT,
        [
            defs.Route(channel="phone", number=THE_CLINICS_NUMBER),
            defs.Route(channel="whatsapp", number=THE_CLINICS_NUMBER),
            defs.Route(channel="web", number=None),
        ],
    )

    route = await answering(routes, registry, THE_CLINICS_NUMBER)

    assert route is not None
    assert route.door == ("whatsapp", THE_CLINICS_NUMBER)


async def test_an_agent_with_only_a_phone_on_that_number_answers_no_whatsapp(
    registry: Registry, routes: MemoryRoutes
) -> None:
    await registry.register(
        AN_APP, A_RECORD.org, AGENT, [defs.Route(channel="phone", number=THE_CLINICS_NUMBER)]
    )

    assert await answering(routes, registry, THE_CLINICS_NUMBER) is None
