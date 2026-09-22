"""call.transfer: the caller's leg sent on with a REFER, or the far end dialled into the room."""

from __future__ import annotations

import asyncio

from livekit.agents.voice import AgentSession
from livekit.protocol.sip import (
    CreateSIPParticipantRequest,
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

# The two modes, and they are two different things happening to the room. COLD is a REFER on the
# caller's own leg: the carrier takes the call, the leg leaves and this session is over. WARM dials
# the far end INTO this room — nobody moves, the agent falls silent, and the call ends when one of
# the two humans hangs up. Warm is the only one a browser can have: there is no SIP leg to REFER.
COLD: TransferMode = "cold"
WARM: TransferMode = "warm"

NO_LEG = "call.transfer: a cold transfer sends the caller's own SIP leg on, and this call has none"
NO_TRUNK = "call.transfer: no outbound SIP trunk is configured, so {to} cannot be dialled"

# The identity the dialled leg takes in the room: livekit's own habit for SIP participants, and
# the same one room.invite uses, so a reader of the log sees one kind of second leg.
LEG_PREFIX = "sip_"

# How long the far end may ring before a warm transfer gives up and the caller is told. LiveKit's
# own default is 30s; a caller holding notices 30s.
RINGING_S = 25.0

# How long the line waits for the agent to finish the sentence that announced the transfer. A
# transfer asked for from inside a tool has already waited: the tool itself does not run until
# the words before it are played out (tools.py). This is for the one sent outside a tool — the
# app's own code, a supervisor at the desk — while the agent happens to be mid-sentence: moving
# the line then cuts the caller off mid-word, which they hear as a dropped call. Bounded: a
# sentence that never ends must not hold a transfer forever.
THE_ANNOUNCEMENT_S = 12.0
A_GLANCE_S = 0.1

# The caller hears a ringing tone while the far end is dialled, instead of a silence they read as
# a dropped call. livekit plays it on the leg being transferred (sip.proto, play_dialtone).
PLAY_DIALTONE = True


# What this call can do, when the app did not say. A caller on a phone is REFERred; a caller in a
# browser has no leg to REFER, so the person is dialled in to them instead. The app may still ask
# for one by name, and a cold transfer of a browser call is refused rather than quietly made warm:
# the two are different calls afterwards, and the log must not say one where the other happened.
async def the_mode(holding: Holding, wanted: CallTransfer) -> TransferMode:
    """The mode to run in: the app's own, else cold for a phone leg and warm for everything else."""
    if wanted.mode is not None:
        return wanted.mode
    return COLD if await sip.the_sip_leg(holding.room, holding.channel) is not None else WARM


# The model says "I am putting you through" and calls the verb in the same reply, so the line must
# not move until that sentence has been heard. livekit's own state is the floor: `speaking` until
# the audio is played out.
async def after_the_announcement(live: AgentSession[None] | None) -> None:
    """Wait for the agent to stop speaking, so a transfer never cuts the caller off mid-word."""
    if live is None:
        return
    waited = 0.0
    while live.agent_state == "speaking" and waited < THE_ANNOUNCEMENT_S:
        await asyncio.sleep(A_GLANCE_S)
        waited += A_GLANCE_S


# The outcome is the wire's own `call.transferred`: whoever asked learns from the log alone. It is
# returned rather than written here so that one place — the applier — writes the entry and ends the
# call, the way every other verb's fact reaches the log through the bridge's one hand.
async def sent_on(holding: Holding, wanted: CallTransfer) -> CallTransferred:
    """TransferSIPParticipant on the caller's leg. ok=False: the caller is still on the line."""
    leg = await sip.the_sip_leg(holding.room, holding.channel)
    if leg is None:
        return _stayed(wanted, NO_LEG, COLD)
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
        return _stayed(wanted, f"{VERB}: {refused}", COLD)
    if answer.status != SIPTransferStatus.STS_TRANSFER_SUCCESSFUL:
        return _stayed(wanted, _did_not_take(answer), COLD)
    return CallTransferred(to=wanted.to, mode=COLD, ok=True)


# `wait_until_answered` is what makes a busy phone and one nobody picks up knowable at all: without
# it the request comes back as soon as the INVITE goes out, and the agent would fall silent for a
# person who never arrives. The dial tone is played to the ROOM while it rings, so the caller hears
# a phone ringing instead of a silence they read as a dropped call.
async def dialled_in(holding: Holding, wanted: CallTransfer) -> CallTransferred:
    """CreateSIPParticipant into this call's own room. ok=True: the person is on the line now."""
    trunk = await holding.trunks.outbound()
    if trunk is None:
        return _stayed(wanted, NO_TRUNK.format(to=wanted.to), WARM)
    request = CreateSIPParticipantRequest(
        sip_trunk_id=trunk,
        sip_call_to=wanted.to,
        room_name=holding.room.name,
        participant_identity=f"{LEG_PREFIX}{wanted.to}",
        play_dialtone=PLAY_DIALTONE,
        wait_until_answered=True,
    )
    request.ringing_timeout.FromSeconds(int(RINGING_S))
    try:
        await holding.api.sip.create_sip_participant(request)
    except Exception as refused:  # noqa: BLE001 — a busy phone and a dead trunk are one outcome
        return _stayed(wanted, f"{VERB}: {refused}", WARM)
    return CallTransferred(to=wanted.to, mode=WARM, ok=True)


# A transfer that fails is not an error entry: the call is still running and the state has to say
# so, which is what `call.transferred` with ok=False does. The agent tells the caller.
def _stayed(wanted: CallTransfer, why: str, mode: TransferMode) -> CallTransferred:
    """The caller stayed on the line, and this is why the transfer did not take."""
    return CallTransferred(to=wanted.to, mode=mode, ok=False, error=why)


# A transfer can fail without raising: the request is accepted, the far end refuses, and livekit
# answers with the status anyway (api/sip_service.py:845-849).
def _did_not_take(answer: TransferSIPParticipantResponse) -> str:
    """Why livekit's own answer says the transfer did not take, with the SIP status behind it."""
    said = SIPTransferReason.Name(answer.reason).removeprefix("STR_").lower()
    status = answer.sip_status
    return f"{VERB}: {said}, SIP {status.code} {status.status}".strip()
