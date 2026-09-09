"""call.transfer, cold: the caller's own leg sent on with a REFER, and how it went."""

from __future__ import annotations

from livekit.protocol.sip import (
    SIPTransferReason,
    SIPTransferStatus,
    TransferSIPParticipantRequest,
    TransferSIPParticipantResponse,
)

from pinecall.session.voice import sip
from pinecall.session.voice.room.holding import Holding
from pinecall_protocol.commands import CallTransfer
from pinecall_protocol.defs import TransferMode
from pinecall_protocol.events import CallTransferred

VERB = "call.transfer"

# The one mode this runtime does. Warm keeps the agent on the line until the far side answers,
# which needs a termination trunk to dial the second leg through and a way to move the caller onto
# it; livekit's MoveParticipant is Cloud-only. See docs/decisions/sip.md.
COLD: TransferMode = "cold"

ONLY_COLD = "call.transfer: only a cold transfer today — warm needs a termination trunk"
NO_LEG = "call.transfer: this call has no SIP leg in the room, so there is nobody to send on"

# The caller hears a ringing tone while the far end is dialled, instead of a silence they read as
# a dropped call. livekit plays it on the leg being transferred (sip.proto, play_dialtone).
PLAY_DIALTONE = True


# The outcome is the wire's own `call.transferred`: whoever asked learns from the log alone. It is
# returned rather than written here so that one place — the applier — writes the entry and ends the
# call, the way every other verb's fact reaches the log through the bridge's one hand.
async def sent_on(holding: Holding, wanted: CallTransfer) -> CallTransferred:
    """TransferSIPParticipant on the caller's leg. ok=False: the caller is still on the line."""
    if wanted.mode != COLD:
        return _stayed(wanted, ONLY_COLD)
    leg = await sip.the_sip_leg(holding.room, holding.channel)
    if leg is None:
        return _stayed(wanted, NO_LEG)
    request = TransferSIPParticipantRequest(
        participant_identity=leg.identity,
        room_name=holding.room.name,
        transfer_to=wanted.to,
        play_dialtone=PLAY_DIALTONE,
    )
    try:
        answer = await holding.api.sip.transfer_sip_participant(request)
    # A dial that the far end refused arrives as livekit's SipCallError, which renders the SIP code
    # and its reason phrase in its own __str__ (api/twirp_client.py:116): the status is in the words
    # already, and every other way the server can say no reads the same to whoever asked.
    except Exception as refused:  # noqa: BLE001 — every way the server says no is one outcome
        return _stayed(wanted, f"{VERB}: {refused}")
    if answer.status != SIPTransferStatus.STS_TRANSFER_SUCCESSFUL:
        return _stayed(wanted, _did_not_take(answer))
    return CallTransferred(to=wanted.to, mode=wanted.mode, ok=True)


# A transfer that fails is not an error entry: the call is still running and the state has to say
# so, which is what `call.transferred` with ok=False does. The agent tells the caller.
def _stayed(wanted: CallTransfer, why: str) -> CallTransferred:
    """The caller stayed on the line, and this is why the transfer did not take."""
    return CallTransferred(to=wanted.to, mode=wanted.mode, ok=False, error=why)


# A transfer can fail without raising: the request is accepted, the far end refuses, and livekit
# answers with the status anyway (api/sip_service.py:845-849).
def _did_not_take(answer: TransferSIPParticipantResponse) -> str:
    """Why livekit's own answer says the transfer did not take, with the SIP status behind it."""
    said = SIPTransferReason.Name(answer.reason).removeprefix("STR_").lower()
    status = answer.sip_status
    return f"{VERB}: {said}, SIP {status.code} {status.status}".strip()
