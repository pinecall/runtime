"""A voice call's room: who sits in it, the caller's tones and codes, transfers, and room verbs."""

import asyncio
import json
from pathlib import Path

import pytest
from livekit import api, rtc
from livekit.agents.voice import AgentSession
from livekit.protocol.room import MuteRoomTrackRequest, RoomParticipantIdentity
from livekit.protocol.sip import (
    CreateSIPParticipantRequest,
    SIPStatusCode,
    SIPTransferReason,
    SIPTransferStatus,
    TransferSIPParticipantRequest,
    TransferSIPParticipantResponse,
)

from pinecall.domain.errors import NotAllowed
from pinecall.domain.types import AgentConfig, Channel
from pinecall.log.store import Store
from pinecall.session import room as room_module
from pinecall.session.call import Call
from pinecall.session.room import (
    CALLER_NUMBER,
    HoldMusic,
    Room,
    Trunk,
    caller_leg,
)
from pinecall.wire.commands import (
    CallDtmf,
    CallTransfer,
    ParticipantMute,
    ParticipantRemove,
    RoomInvite,
    RoomSend,
)
from tests.conftest import postgres
from tests.fakes import Player, Server, microphone, seat
from tests.fakes import Room as AnOfflineRoom
from tests.session.conftest import THE_CALLER, Box, context_of, heard_live

SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
AGENT = AgentConfig(slug="clinica-norte")


def _the_caller() -> rtc.RemoteParticipant:
    return seat("sip_caller", kind=SIP, attributes={CALLER_NUMBER: THE_CALLER})


A_TRUNK = Trunk("ST_1", "+59829001199")


class Asked:
    """The gateway's answers to a room: a trunk to dial through, and the codes claimed."""

    def __init__(self, trunk: Trunk | None = A_TRUNK) -> None:
        """A gateway that dials through the trunk, or refuses when there is none."""
        self.trunk = trunk
        self.claimed: list[str] = []

    async def trunks(self, to: str) -> Trunk:
        """The trunk, or the org's guards' refusal."""
        if self.trunk is None:
            raise NotAllowed(f"{to} is outside this org's dial guards")
        return self.trunk

    async def claim(self, code: str) -> None:
        """Keep the code; 0000 is no code at all."""
        self.claimed.append(code)
        if code == "0000":
            raise NotAllowed("no such code")


def _room(
    box: Box,
    server: Server,
    *seats: rtc.RemoteParticipant,
    channel: Channel = "phone",
    asked: Asked | None = None,
) -> tuple[Room, AnOfflineRoom, Asked]:
    assert box.log.call is not None
    call = Call(context_of(box.log.call, channel), AGENT, box.platform())
    call.writing.open()
    offline, answering = AnOfflineRoom(box.log.call, *seats), asked or Asked()
    where = Room(call, offline, server, trunks=answering.trunks, claim=answering.claim)
    return where, offline, answering


async def _written(where: Room, store: Store) -> list[tuple[str, dict[str, object]]]:
    await where.call.writing.flushed(5)
    assert where.call.context.call is not None
    return [(entry.type, dict(entry.data)) for entry in await store.whole(where.call.context.call)]


# ── who sits in the room ──


@postgres
async def test_a_caller_joining_speaking_and_leaving_is_four_facts_in_that_order(
    box: Box, server: Server
) -> None:
    seen = heard_live(box)
    where, offline, _ = _room(box, server)
    where.watch()
    caller = _the_caller()
    offline.emit("participant_connected", caller)
    offline.emit("active_speakers_changed", [caller])
    offline.emit("active_speakers_changed", [])
    offline.emit("participant_disconnected", seat("sip_caller", kind=SIP, reason=1))
    await where.call.writing.flushed(5)
    written = [entry.type for entry in seen]
    assert written == [
        "participant.joined",
        "participant.speaking",
        "participant.speaking",
        "participant.left",
    ]


@postgres
async def test_the_sip_attributes_travel_verbatim_and_the_caller_is_read_from_them(
    box: Box, server: Server, store: Store
) -> None:
    where, offline, _ = _room(box, server)
    where.watch()
    offline.emit("participant_connected", _the_caller())
    ((_, joined),) = await _written(where, store)
    assert joined["kind"] == "caller"
    assert joined["attributes"] == {CALLER_NUMBER: THE_CALLER}


@postgres
async def test_a_second_sip_leg_is_kind_sip_and_never_the_caller(
    box: Box, server: Server, store: Store
) -> None:
    where, offline, _ = _room(box, server)
    where.watch()
    offline.emit(
        "participant_connected",
        seat("sip_+598", kind=SIP, attributes={CALLER_NUMBER: "+59829000000"}),
    )
    ((_, joined),) = await _written(where, store)
    assert joined["kind"] == "sip"


@postgres
async def test_a_token_scope_says_who_took_the_seat(box: Box, server: Server, store: Store) -> None:
    where, offline, _ = _room(box, server)
    where.watch()
    offline.emit("participant_connected", seat("ana", attributes={"pinecall.scope": "supervise"}))
    offline.emit("participant_connected", seat("beto", attributes={"pinecall.scope": "observe"}))
    kinds = [data["kind"] for _, data in await _written(where, store)]
    assert kinds == ["supervisor", "listener"]


@postgres
async def test_a_widget_is_the_caller_of_a_web_call(box: Box, server: Server, store: Store) -> None:
    where, offline, _ = _room(box, server, channel="web")
    where.watch()
    offline.emit("participant_connected", seat("visitor_1", attributes={"pinecall.scope": "talk"}))
    ((_, joined),) = await _written(where, store)
    assert joined["kind"] == "caller"


@postgres
async def test_speaking_is_written_on_the_change_and_not_on_every_tick(
    box: Box, server: Server
) -> None:
    seen = heard_live(box)
    where, offline, _ = _room(box, server)
    where.watch()
    caller = _the_caller()
    for _ in range(3):
        offline.emit("active_speakers_changed", [caller])
    await where.call.writing.flushed(5)
    speaking = [entry.data for entry in seen if entry.type == "participant.speaking"]
    assert speaking == [{"identity": "sip_caller", "speaking": True}]


@postgres
async def test_a_tone_the_caller_keys_is_written_and_one_from_another_leg_is_not(
    box: Box, server: Server, store: Store
) -> None:
    where, offline, _ = _room(box, server)
    where.watch()
    offline.emit("sip_dtmf_received", rtc.SipDTMF(code=5, digit="5", participant=_the_caller()))
    other = seat("sip_+598", kind=SIP, attributes={CALLER_NUMBER: "+59829000000"})
    offline.emit("sip_dtmf_received", rtc.SipDTMF(code=6, digit="6", participant=other))
    offline.emit("sip_dtmf_received", rtc.SipDTMF(code=7, digit="7", participant=None))
    tones = [data for kind, data in await _written(where, store) if kind == "dtmf.received"]
    assert tones == [{"digit": "5", "code": 5}]


@postgres
async def test_stopping_lets_go_of_the_room(box: Box, server: Server, store: Store) -> None:
    where, offline, _ = _room(box, server)
    where.watch()
    where.stop()
    offline.emit("participant_connected", _the_caller())
    assert await _written(where, store) == []


# ── the caller's codes ──


def _keyed(offline: AnOfflineRoom, digits: str) -> None:
    for digit in digits:
        code = 10 if digit == "*" else 11 if digit == "#" else int(digit)
        offline.emit(
            "sip_dtmf_received", rtc.SipDTMF(code=code, digit=digit, participant=_the_caller())
        )


@postgres
async def test_four_tones_within_the_window_are_one_claim_and_the_same_code_is_not_asked_twice(
    box: Box, server: Server
) -> None:
    where, offline, asked = _room(box, server)
    where.watch()
    _keyed(offline, "12341234")
    await asyncio.sleep(0.02)
    assert asked.claimed == ["1234"]


@postgres
async def test_a_star_or_a_pound_starts_the_code_over(box: Box, server: Server) -> None:
    where, offline, asked = _room(box, server)
    where.watch()
    _keyed(offline, "12*3456")
    await asyncio.sleep(0.02)
    assert asked.claimed == ["3456"]


@postgres
async def test_a_refused_claim_is_no_error(box: Box, server: Server, store: Store) -> None:
    where, offline, asked = _room(box, server)
    where.watch()
    _keyed(offline, "0000")
    await asyncio.sleep(0.02)
    assert asked.claimed == ["0000"]
    assert [kind for kind, _ in await _written(where, store) if kind == "error"] == []


# ── the caller's leg ──


async def test_the_leg_already_seated_when_the_agent_arrives_is_found_without_waiting() -> None:
    offline = AnOfflineRoom("call_1", _the_caller())
    found = await asyncio.wait_for(caller_leg(offline, "phone"), 0.5)
    assert found is not None
    assert found.identity == "sip_caller"


async def test_a_call_that_is_not_a_phones_is_answered_at_once_with_no_leg() -> None:
    assert await asyncio.wait_for(caller_leg(AnOfflineRoom("call_1"), "web"), 0.5) is None


async def test_a_room_that_never_connected_has_no_leg_either() -> None:
    assert await asyncio.wait_for(caller_leg(AnOfflineRoom("call_1"), "phone"), 6) is None


# ── transfers ──


@postgres
async def test_a_cold_transfer_refers_the_callers_own_leg_and_says_it_took(
    box: Box, server: Server
) -> None:
    where, _, _ = _room(box, server, _the_caller())
    done = await where.transfer(CallTransfer(to="+59829000000", mode="cold"), AgentSession())
    assert (done.ok, done.mode) == (True, "cold")
    (asked,) = server.dialled.asked
    assert isinstance(asked, TransferSIPParticipantRequest)
    assert (asked.participant_identity, asked.transfer_to, asked.play_dialtone) == (
        "sip_caller",
        "+59829000000",
        True,
    )


@postgres
async def test_a_phone_call_that_names_no_mode_is_referred_and_a_browser_call_is_dialled(
    box: Box, server: Server
) -> None:
    phone, _, _ = _room(box, server, _the_caller())
    web, _, _ = _room(box, server, channel="web")
    assert await phone.mode_of(CallTransfer(to="+598")) == "cold"
    assert await web.mode_of(CallTransfer(to="+598")) == "warm"


@postgres
async def test_a_warm_transfer_dials_the_person_in_and_waits_for_them_to_answer(
    box: Box, server: Server
) -> None:
    where, _, _ = _room(box, server, channel="web")
    done = await where.transfer(CallTransfer(to="+59829000000"), AgentSession())
    assert (done.ok, done.mode) == (True, "warm")
    (asked,) = server.dialled.asked
    assert isinstance(asked, CreateSIPParticipantRequest)
    assert (asked.sip_trunk_id, asked.sip_number, asked.participant_identity) == (
        "ST_1",
        "+59829001199",
        "sip_+59829000000",
    )
    assert asked.wait_until_answered
    assert asked.ringing_timeout.seconds == 25


@postgres
async def test_a_warm_transfer_the_orgs_guards_refused_says_what_they_said(
    box: Box, server: Server
) -> None:
    where, _, _ = _room(box, server, channel="web", asked=Asked(trunk=None))
    done = await where.transfer(CallTransfer(to="+34600000000"), AgentSession())
    assert (done.ok, done.error) == (False, "+34600000000 is outside this org's dial guards")
    assert server.dialled.asked == []


@postgres
async def test_a_cold_transfer_of_a_call_with_no_sip_leg_leaves_the_caller_where_they_are(
    box: Box, server: Server
) -> None:
    where, _, _ = _room(box, server, channel="web")
    done = await where.transfer(CallTransfer(to="+598", mode="cold"), AgentSession())
    assert (done.ok, done.error) == (False, "call.transfer: this call has no phone leg")
    assert server.dialled.asked == []


@postgres
async def test_a_far_end_that_refuses_the_dial_carries_its_status_into_the_log(
    box: Box, server: Server
) -> None:
    where, _, _ = _room(box, server, channel="web")
    server.dialled.refusal = api.TwirpError("unavailable", "486 Busy Here", status=503)
    done = await where.transfer(CallTransfer(to="+598"), AgentSession())
    assert done.ok is False
    assert "486 Busy Here" in str(done.error)


@postgres
async def test_a_transfer_livekit_answered_on_but_that_did_not_take_says_why(
    box: Box, server: Server
) -> None:
    where, _, _ = _room(box, server, _the_caller())
    server.dialled.answer = TransferSIPParticipantResponse(
        status=SIPTransferStatus.STS_TRANSFER_FAILED, reason=SIPTransferReason.STR_REJECTED
    )
    server.dialled.answer.sip_status.code = SIPStatusCode.SIP_STATUS_GLOBAL_DECLINE
    server.dialled.answer.sip_status.status = "Decline"
    done = await where.transfer(CallTransfer(to="+598", mode="cold"), AgentSession())
    assert (done.ok, done.error) == (False, "call.transfer: rejected, SIP 603 Decline")


@postgres
async def test_the_person_leaving_after_a_warm_transfer_ends_the_call(
    box: Box, server: Server
) -> None:
    where, offline, _ = _room(box, server, channel="web")
    ended: list[bool] = []
    where.when_the_person_leaves("sip_+598", lambda: ended.append(True))
    offline.emit("participant_disconnected", seat("visitor_1"))
    offline.emit("participant_disconnected", seat("sip_+598", kind=SIP))
    assert ended == [True]


# ── the other verbs ──


@postgres
async def test_room_invite_dials_a_sip_leg_into_this_room(box: Box, server: Server) -> None:
    where, _, _ = _room(box, server)
    await where.apply(RoomInvite(to="+59829000000", kind="sip"))
    (asked,) = server.dialled.asked
    assert isinstance(asked, CreateSIPParticipantRequest)
    assert (asked.room_name, asked.sip_call_to) == (where.room.name, "+59829000000")


@postgres
async def test_room_invite_of_a_participant_is_refused_by_name_without_dialling(
    box: Box, server: Server, store: Store
) -> None:
    where, _, _ = _room(box, server)
    await where.apply(RoomInvite(to="ana", kind="participant"))
    assert server.dialled.asked == []
    ((kind, error),) = await _written(where, store)
    assert (kind, error["code"], error["command"]) == ("error", "room_verb_failed", "room.invite")


@postgres
async def test_room_invite_with_no_trunk_lands_an_error_naming_the_verb(
    box: Box, server: Server, store: Store
) -> None:
    where, _, _ = _room(box, server, asked=Asked(trunk=None))
    await where.apply(RoomInvite(to="+34600000000", kind="sip"))
    ((_, error),) = await _written(where, store)
    assert (error["command"], error["recoverable"]) == ("room.invite", True)


@postgres
async def test_participant_mute_of_somebody_with_no_microphone_is_an_error_too(
    box: Box, server: Server, store: Store
) -> None:
    where, _, _ = _room(box, server, _the_caller())
    await where.apply(ParticipantMute(identity="sip_caller"))
    assert server.rooms.asked == []
    ((_, error),) = await _written(where, store)
    assert error["message"] == "participant.mute: sip_caller has no microphone in the room"


@postgres
async def test_participant_mute_of_somebody_not_in_the_room_is_an_error_not_a_call(
    box: Box, server: Server, store: Store
) -> None:
    where, _, _ = _room(box, server)
    await where.apply(ParticipantMute(identity="nobody"))
    assert server.rooms.asked == []
    ((_, error),) = await _written(where, store)
    assert error["message"] == "participant.mute: nobody is not in the room"


@postgres
async def test_participant_remove_puts_them_out(box: Box, server: Server) -> None:
    where, _, _ = _room(box, server)
    await where.apply(ParticipantRemove(identity="sip_+598"))
    (asked,) = server.rooms.asked
    assert isinstance(asked, RoomParticipantIdentity)
    assert asked.identity == "sip_+598"


@postgres
async def test_room_send_publishes_on_the_topic_and_logs_the_size_never_the_payload(
    box: Box, server: Server
) -> None:
    seen = heard_live(box)
    where, offline, _ = _room(box, server)
    await where.apply(RoomSend(topic="pinecall.ui", data={"card": "secreto"}, to="visitor_1"))
    ((topic, packed, to),) = offline.me.published
    assert (topic, json.loads(packed), to) == ("pinecall.ui", {"card": "secreto"}, ["visitor_1"])
    await where.call.writing.flushed(5)
    (sent,) = [entry for entry in seen if entry.type == "room.sent"]
    assert (sent.type, sent.data) == (
        "room.sent",
        {"topic": "pinecall.ui", "to": "visitor_1", "bytes": len(packed)},
    )


@postgres
async def test_room_send_to_nobody_in_particular_reaches_the_whole_room(
    box: Box, server: Server
) -> None:
    where, offline, _ = _room(box, server)
    await where.apply(RoomSend(topic="pinecall.ui", data={}))
    assert offline.me.published[0][2] == []


@postgres
async def test_every_tone_goes_down_the_line_in_the_order_it_was_given_and_a_comma_pauses(
    box: Box, server: Server
) -> None:
    where, offline, _ = _room(box, server, _the_caller())
    await where.apply(CallDtmf(digits="12,#"))
    assert offline.me.tones == ["1", "2", "#"]


@postgres
async def test_something_that_is_not_a_touch_tone_sends_nothing_at_all(
    box: Box, server: Server, store: Store
) -> None:
    where, offline, _ = _room(box, server, _the_caller())
    await where.apply(CallDtmf(digits="12a3"))
    assert offline.me.tones == []
    ((_, error),) = await _written(where, store)
    assert (
        error["message"] == "call.dtmf: 'a' is not a touch tone (0-9, * or #), and nothing was sent"
    )


@postgres
async def test_a_call_with_no_phone_leg_has_nothing_to_send_tones_down(
    box: Box, server: Server, store: Store
) -> None:
    where, offline, _ = _room(box, server, channel="web")
    await where.apply(CallDtmf(digits="1"))
    assert offline.me.tones == []
    ((_, error),) = await _written(where, store)
    assert error["message"] == "call.dtmf: this call has no phone leg"


@postgres
async def test_a_server_that_says_no_lands_an_error_naming_the_verb_and_the_call_goes_on(
    box: Box, server: Server, store: Store
) -> None:
    where, _, _ = _room(box, server)
    server.dialled.refusal = api.TwirpError("permission_denied", "trunk is disabled", status=403)
    await where.apply(RoomInvite(to="+598", kind="sip"))
    ((_, error),) = await _written(where, store)
    assert (error["command"], error["recoverable"]) == ("room.invite", True)
    assert "trunk is disabled" in str(error["message"])


# ── the melody ──


async def test_a_tool_that_answers_inside_the_grace_plays_nothing() -> None:
    melody = HoldMusic(Path(__file__))
    melody.began()
    melody.ended()
    assert (melody.running, melody.pending, melody.handle) == (0, None, None)


async def test_tools_side_by_side_share_one_melody_until_the_last_ends() -> None:
    melody = HoldMusic(Path(__file__))
    melody.began()
    melody.began()
    melody.ended()
    assert melody.running == 1
    melody.ended()
    assert melody.running == 0


async def test_a_tool_that_takes_a_while_plays_the_melody_looped_and_stops_it_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(room_module, "GRACE_S", 0.01)
    melody = HoldMusic(Path("hold.ogg"))
    melody.player = Player()
    melody.began()
    await asyncio.sleep(0.05)
    assert melody.player.played == [("hold.ogg", True)]
    melody.ended()
    assert melody.player.handles[0].done()


async def test_the_melody_waits_for_the_agent_to_stop_talking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(room_module, "GRACE_S", 0.01)
    melody = HoldMusic(Path("hold.ogg"))
    player = Player()
    melody.player = player
    melody.floor(speaking=True)
    melody.began()
    await asyncio.sleep(0.05)
    assert player.played == []
    melody.floor(speaking=False)
    await asyncio.sleep(0.05)
    assert player.played == [("hold.ogg", True)]
    melody.ended()


async def test_the_grace_outlasts_the_line_the_agent_says_before_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(room_module, "GRACE_S", 0.05)
    melody = HoldMusic(Path("hold.ogg"))
    player = Player()
    melody.player = player
    melody.began()
    await asyncio.sleep(0.01)
    melody.floor(speaking=True)
    await asyncio.sleep(0.08)
    assert player.played == []
    melody.ended()


@postgres
async def test_tones_too_far_apart_are_not_a_code(
    box: Box, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(room_module, "CODE_WITHIN_S", -1.0)
    where, offline, asked = _room(box, server)
    where.watch()
    _keyed(offline, "1234")
    await asyncio.sleep(0.02)
    assert asked.claimed == []


@postgres
async def test_participant_mute_mutes_their_microphone_and_writes_track_unpublished(
    box: Box, server: Server
) -> None:
    seen = heard_live(box)
    where, _, _ = _room(box, server, microphone("ana"))
    await where.apply(ParticipantMute(identity="ana"))
    (asked,) = server.rooms.asked
    assert isinstance(asked, MuteRoomTrackRequest)
    assert (asked.identity, asked.track_sid, asked.muted) == ("ana", "TR_ana", True)
    await where.call.writing.flushed(5)
    (unpublished,) = [entry.data for entry in seen if entry.type == "track.unpublished"]
    assert unpublished == {"identity": "ana", "kind": "audio", "source": "microphone"}
