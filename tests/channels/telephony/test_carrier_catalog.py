"""Tests for the carriers a box knows and which of them its operator admits."""

import pytest

from pinecall.channels.telephony import carrier_catalog
from pinecall.channels.telephony.carrier import TWILIO_SIGNALLING
from pinecall.domain.errors import Conflict, NotFound
from pinecall.postgres.pool import Pool
from tests.conftest import postgres


def test_the_catalog_reads_each_carrier_with_its_page_and_day() -> None:
    known = carrier_catalog.known()
    assert list(known) == ["twilio", "telnyx", "vonage", "plivo"]
    assert known["twilio"].control
    assert not known["telnyx"].control
    assert known["twilio"].networks == TWILIO_SIGNALLING
    assert len(known["telnyx"].networks) == 12
    assert known["telnyx"].source == "https://sip.telnyx.com/"
    assert known["vonage"].read_on.isoformat() == "2026-10-03"


def test_a_kind_the_catalog_lacks_is_named_with_the_ones_it_has() -> None:
    with pytest.raises(NotFound, match="twilio, telnyx"):
        carrier_catalog.known_carrier("bandwidth")


@postgres
async def test_twilio_is_admitted_always_and_another_carrier_by_the_operator(pool: Pool) -> None:
    assert await carrier_catalog.admitted(pool) == {"twilio"}
    assert await carrier_catalog.admit(pool, "telnyx", on=True) == {"twilio", "telnyx"}
    assert await carrier_catalog.admitted(pool) == {"twilio", "telnyx"}
    assert await carrier_catalog.admit(pool, "telnyx", on=False) == {"twilio"}
    with pytest.raises(Conflict, match="always admitted"):
        await carrier_catalog.admit(pool, "twilio", on=False)
    with pytest.raises(NotFound):
        await carrier_catalog.admit(pool, "bandwidth", on=True)
