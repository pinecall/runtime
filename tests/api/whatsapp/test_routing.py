"""Which route answers a WhatsApp number: a row an operator typed, and nothing else."""

from __future__ import annotations

import logging

import pytest

from pinecall.routes.records import MemoryRoutes
from pinecall.types import PRODUCTION, Route
from pinecall.whatsapp.routing import answering
from tests.api.conftest import A_RECORD, AGENT
from tests.api.whatsapp.conftest import THE_CLINICS_NUMBER

pytestmark = pytest.mark.unit


# A door is the channel AND the number, which is what a clinic with its phone and its WhatsApp on
# one number needs: the first live signed body found the phone route answering a WhatsApp message.
async def test_a_number_that_answers_both_is_two_rows_and_whatsapp_reads_its_own() -> None:
    table = MemoryRoutes(
        [
            Route(org=A_RECORD.org, agent=AGENT, channel="phone", number=THE_CLINICS_NUMBER),
            Route(org=A_RECORD.org, agent=AGENT, channel="whatsapp", number=THE_CLINICS_NUMBER),
        ]
    )

    route = await answering(table, THE_CLINICS_NUMBER)

    assert route is not None
    assert route.door == ("whatsapp", THE_CLINICS_NUMBER)


async def test_a_number_with_only_a_phone_row_answers_no_whatsapp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """And says what to type: Meta gets its 200 either way, so a silence here is for good."""
    table = MemoryRoutes(
        [Route(org=A_RECORD.org, agent=AGENT, channel="phone", number=THE_CLINICS_NUMBER)]
    )

    with caplog.at_level(logging.WARNING):
        assert await answering(table, THE_CLINICS_NUMBER) is None

    assert THE_CLINICS_NUMBER in caplog.text
    assert "routes add" in caplog.text


async def test_a_number_nobody_typed_answers_nothing() -> None:
    assert await answering(MemoryRoutes(), THE_CLINICS_NUMBER) is None


def test_the_world_a_row_names_is_the_world_the_thread_runs_in() -> None:
    """A door is one agent's in one world: the row carries it, and the message follows the row."""
    row = Route(org=A_RECORD.org, agent=AGENT, channel="whatsapp", number=THE_CLINICS_NUMBER)
    assert row.env == PRODUCTION
