"""Cold transfer: the REFER on the caller's leg, and every way it does not take."""

from __future__ import annotations

from typing import Any

import pytest
from livekit.api import SipCallError
from livekit.protocol.sip import (
    SIPStatus,
    SIPStatusCode,
    SIPTransferReason,
    SIPTransferStatus,
    TransferSIPParticipantResponse,
)

from pinecall.session.voice import commands
from pinecall_protocol import Command
from tests.session.voice.room.fakes import FakeApi, Held, a_caller, a_held_room
from tests.session.voice.test_commands import End, Prompt, Recorded, Session

pytestmark = pytest.mark.unit

CALLER = "+59897777"
THE_DESK = "+59892222"


async def test_a_cold_transfer_refers_the_callers_own_leg_and_the_log_says_it_took() -> None:
    held = a_phone_call()
    end = await applied(held, {"to": THE_DESK, "mode": "cold"})
    (referred,) = held.api.requests
    assert referred.method == "TransferSIPParticipant"
    assert referred.request.participant_identity == f"sip_{CALLER}"
    assert (referred.request.room_name, referred.request.transfer_to) == (held.room.name, THE_DESK)
    assert referred.request.play_dialtone
    (said,) = held.recording.of("call.transferred")
    assert (said.data["to"], said.data["mode"], said.data["ok"]) == (THE_DESK, "cold", True)
    assert end.transfers == 1


async def test_a_warm_transfer_is_refused_by_name_without_dialling_anybody() -> None:
    held = a_phone_call()
    end = await applied(held, {"to": THE_DESK, "mode": "warm"})
    assert held.api.requests == []
    (said,) = held.recording.of("call.transferred")
    assert said.data["ok"] is False
    assert "warm" in said.data["error"]
    assert end.transfers == 0


async def test_a_call_with_no_sip_leg_leaves_the_caller_where_they_are() -> None:
    held = a_held_room(channel="web")
    held.room.connect()
    end = await applied(held, {"to": THE_DESK, "mode": "cold"})
    assert held.api.requests == []
    (said,) = held.recording.of("call.transferred")
    assert said.data["ok"] is False
    assert end.transfers == 0


async def test_a_far_end_that_refuses_the_dial_carries_its_sip_status_into_the_log() -> None:
    held = a_phone_call(api=FakeApi(refusing=_busy_here()))
    end = await applied(held, {"to": THE_DESK, "mode": "cold"})
    (said,) = held.recording.of("call.transferred")
    assert said.data["ok"] is False
    assert "486" in said.data["error"]
    assert "Busy Here" in said.data["error"]
    assert end.transfers == 0


async def test_a_transfer_livekit_answered_on_but_that_did_not_take_says_why_it_did_not() -> None:
    held = a_phone_call(api=FakeApi(answering=_rejected()))
    end = await applied(held, {"to": THE_DESK, "mode": "cold"})
    assert held.api.requests != []
    (said,) = held.recording.of("call.transferred")
    assert said.data["ok"] is False
    assert "rejected" in said.data["error"]
    assert "486 Busy Here" in said.data["error"]
    assert end.transfers == 0


def a_phone_call(api: FakeApi | None = None) -> Held:
    """A room with the caller's own leg in it, as a phone call has before anybody speaks."""
    held = a_held_room(api=api)
    held.room.connect()
    held.room.join(a_caller(CALLER))
    return held


async def applied(held: Held, data: dict[str, Any]) -> End:
    """call.transfer through the dispatch table, onto this held room; the ending it reached."""
    end = End()
    applying = commands.Applying(Session(), Prompt(), end, Recorded(), held.holding)  # pyright: ignore[reportArgumentType]
    command = Command(type="call.transfer", agent="clinica-norte", call="call_1", data=data)
    await commands.apply(applying, command)
    await held.settled()
    return end


def _busy_here() -> SipCallError:
    """The dial raised: livekit hands back the SIP status the far end answered with."""
    return SipCallError(
        "unavailable",
        "sip: dial failed",
        status=503,
        metadata={"sip_status_code": "486", "sip_status": "Busy Here"},
    )


def _rejected() -> TransferSIPParticipantResponse:
    """The dial did not raise: livekit answered, and its own answer says the transfer failed."""
    return TransferSIPParticipantResponse(
        status=SIPTransferStatus.STS_TRANSFER_FAILED,
        reason=SIPTransferReason.STR_REJECTED,
        sip_status=SIPStatus(code=SIPStatusCode.SIP_STATUS_BUSY_HERE, status="Busy Here"),
    )
